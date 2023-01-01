'''
Copyright (C) 2021 CG Cookie
http://cgcookie.com
hello@cgcookie.com

Created by Jonathan Denning, Jonathan Williamson, and Patrick Moore

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''

import math
import time
import bgl
import bpy
from math import isnan

from contextlib import contextmanager

from mathutils import Vector, Matrix
from mathutils.geometry import intersect_point_tri_2d

from ..rftool import RFTool
from ..rfwidget import RFWidget
from ..rfwidgets.rfwidget_default     import RFWidget_Default_Factory
from ..rfwidgets.rfwidget_brushstroke import RFWidget_BrushStroke_Factory
from ..rfwidgets.rfwidget_hidden      import RFWidget_Hidden_Factory


from ...addon_common.common.debug import dprint
from ...addon_common.common.fsm import FSM
from ...addon_common.common.globals import Globals
from ...addon_common.common.profiler import profiler
from ...addon_common.common.maths import (
    Point, Vec, Direction,
    Point2D, Vec2D,
    Accel2D,
    clamp, mid,
)
from ...addon_common.common.bezier import CubicBezierSpline, CubicBezier
from ...addon_common.common.utils import iter_pairs, iter_running_sum, min_index, max_index, has_duplicates
from ...addon_common.common.boundvar import BoundBool, BoundInt, BoundFloat
from ...addon_common.common.drawing import DrawCallbacks
from ...config.options import options, themes

from .autofill_utils import (
    AutofillPatches, Side,
    process_stroke_filter, process_stroke_source,
    find_edge_cycles,
    find_edge_strips, get_strip_verts,
    restroke, walk_to_corner,
)

class Autofill(RFTool):
    name        = 'Autofill'
    description = 'Draw patches and autofill them'
    icon        = 'strokes-icon.png'
    help        = 'strokes.md'
    shortcut    = 'autofill tool'
    statusbar   = '{{insert}} Insert edge strip and bridge\t{{increase count}} Increase segments\t{{decrease count}} Decrease segments'
    # ui_config   = 'strokes_options.html'

    RFWidget_Default     = RFWidget_Default_Factory.create()
    RFWidget_Move        = RFWidget_Default_Factory.create(cursor='HAND')
    RFWidget_Hidden      = RFWidget_Hidden_Factory.create()
    RFWidget_BrushStroke = RFWidget_BrushStroke_Factory.create(
        'Autofill stroke',
        BoundInt('''options['strokes radius']''', min_value=1),
        outer_border_color=themes['strokes'], # TODO: add theme for autofill
    )
    
    @property
    def cross_count(self):
        return self.strip_crosses or 0
    @cross_count.setter
    def cross_count(self, v):
        if self.strip_crosses == v: return
        if self.replay is None: return
        if self.strip_crosses is None: return
        self.strip_crosses = v
        if self.strip_crosses is not None: self.replay()

    @property
    def loop_count(self):
        return self.strip_loops or 0
    @loop_count.setter
    def loop_count(self, v):
        if self.strip_loops == v: return
        if self.replay is None: return
        if self.strip_loops is None: return
        self.strip_loops = v
        if self.strip_loops is not None: self.replay()

    @RFTool.on_init
    def init(self):
        self.rfwidgets = {
            'default': self.RFWidget_Default(self),
            'brush':   self.RFWidget_BrushStroke(self),
            'hover':   self.RFWidget_Move(self),
            'hidden':  self.RFWidget_Hidden(self),
        }
        self.rfwidget = None
        self.strip_crosses = None
        self.strip_loops = None
        self.patches = AutofillPatches()
        self._var_fixed_span_count = BoundInt('''options['strokes span count']''', min_value=1, max_value=128)
        self._var_cross_count = BoundInt('''self.cross_count''', min_value=1, max_value=500)
        self._var_loop_count  = BoundInt('''self.loop_count''', min_value=1, max_value=500)

    @contextmanager
    def defer_recomputing_while(self):
        try:
            self.defer_recomputing = True
            yield
        finally:
            self.defer_recomputing = False
            self.update()

    def update_span_mode(self):
        mode = options['strokes span insert mode']
        self.ui_summary.innerText = f'Strokes: {mode}'
        self.ui_insert.dirty(cause='insert mode change', children=True)

    @RFTool.on_ui_setup
    def ui(self):
        # ui_options = self.document.body.getElementById('strokes-options')
        # self.ui_summary = ui_options.getElementById('strokes-summary')
        # self.ui_insert = ui_options.getElementById('strokes-insert-modes')
        # self.ui_radius = ui_options.getElementById('strokes-radius')
        # def dirty_radius():
        #     return #self.ui_radius.dirty(cause='radius changed')
        # self.rfwidgets['brush'].get_radius_boundvar().on_change(dirty_radius)
        # self.update_span_mode()
        return

    @RFTool.on_reset
    def reset(self):
        self.replay = None
        self.strip_crosses = None
        self.strip_loops = None
        self.strip_edges = False
        self.just_created = False
        self.defer_recomputing = False
        self.hovering_edge = None
        self.hovering_edge_time = 0
        self.hovering_sel_edge = None
        self.connection_pre = None
        self.connection_pre_time = 0
        self.connection_post = None
        self.update_ui()

    def update_ui(self):
        if self.replay is None:
            self._var_cross_count.disabled = True
            self._var_loop_count.disabled = True
        else:
            self._var_cross_count.disabled = self.strip_crosses is None or self.strip_edges
            self._var_loop_count.disabled = self.strip_loops is None

    @RFTool.on_target_change
    def update_target(self):
        if self.defer_recomputing: return
        if not self.just_created: self.reset()
        else: self.just_created = False

    @RFTool.on_target_change
    @RFTool.on_view_change
    def update(self):
        if self.defer_recomputing: return

        self.update_ui()

        self.edge_collections = []
        edges = self.get_edges_for_extrude()
        while edges:
            current = set()
            working = set([edges.pop()])
            while working:
                e = working.pop()
                if e in current: continue
                current.add(e)
                edges.discard(e)
                v0,v1 = e.verts
                working |= {e for e in (v0.link_edges + v1.link_edges) if e in edges}
            ctr = Point.average(v.co for v in {v for e in current for v in e.verts})
            self.edge_collections.append({
                'edges': current,
                'center': ctr,
            })

    def filter_edge_selection(self, bme):
        return bme.select or len(bme.link_faces) < 2

    @FSM.on_state('main')
    def modal_main(self):
        if not self.actions.using('action', ignoredrag=True):
            # only update while not pressing action, because action includes drag, and
            # the artist might move mouse off selected edge before drag kicks in!
            if time.time() - self.hovering_edge_time > 0.125:
                self.hovering_edge_time = time.time()
                self.hovering_edge,_     = self.rfcontext.accel_nearest2D_edge(max_dist=options['action dist'])
                self.hovering_sel_edge,_ = self.rfcontext.accel_nearest2D_edge(max_dist=options['action dist'], selected_only=True)
            pass

        self.connection_post = None
        if self.actions.using_onlymods('insert'):
            if time.time() - self.connection_pre_time > 0.01:
                self.connection_pre_time = time.time()
                hovering_sel_vert_snap,_ = self.rfcontext.accel_nearest2D_vert(max_dist=options['strokes snap dist'])
                if options['strokes snap stroke'] and hovering_sel_vert_snap:
                    self.connection_pre = (
                        self.rfcontext.Point_to_Point2D(hovering_sel_vert_snap.co),
                        self.actions.mouse,
                    )
                else:
                    self.connection_pre = None
        else:
            self.connection_pre = None

        if self.actions.using_onlymods('insert'):
            self.set_widget('brush')
        elif self.hovering_sel_edge:
            self.set_widget('hover')
        else:
            self.set_widget('default')

        if self.handle_inactive_passthrough(): return

        if self.rfcontext.actions.pressed('pie menu alt0'):
            def callback(option):
                if not option: return
                options['strokes span insert mode'] = option
                self.update_span_mode()
            self.rfcontext.show_pie_menu([
                'Brush Size',
                'Fixed',
            ], callback, highlighted=options['strokes span insert mode'])
            return

        if self.hovering_sel_edge:
            if self.actions.pressed('action'):
                self.move_done_pressed = None
                self.move_done_released = 'action'
                self.move_cancelled = 'cancel'
                return 'move'

        if self.actions.pressed({'select path add'}):
            return self.rfcontext.select_path(
                {'edge'},
                fn_filter_bmelem=self.filter_edge_selection,
                kwargs_select={'supparts': False},
            )

        if self.actions.pressed({'select paint', 'select paint add'}, unpress=False):
            sel_only = self.actions.pressed('select paint')
            self.actions.unpress()
            return self.rfcontext.setup_smart_selection_painting(
                {'edge'},
                selecting=not sel_only,
                deselect_all=sel_only,
                fn_filter_bmelem=self.filter_edge_selection,
                kwargs_select={'supparts': False},
                kwargs_deselect={'subparts': False},
            )

        if self.actions.pressed({'select single', 'select single add'}, unpress=False):
            print("click")
            sel_only = self.actions.pressed('select single')
            self.actions.unpress()
            bmf,_ = self.rfcontext.accel_nearest2D_face(max_dist=options['select dist'])
            sel = self.hovering_edge or bmf
            print(sel)
            if not sel_only and not sel: return
            self.rfcontext.undo_push('select')
            if sel_only: self.rfcontext.deselect_all()
            if not sel: return
            if sel.select: self.rfcontext.deselect(sel)
            else:                         self.rfcontext.select(sel, supparts=False, only=sel_only)
            return


        if self.rfcontext.actions.pressed({'select smart', 'select smart add'}, unpress=False):
            sel_only = self.rfcontext.actions.pressed('select smart')
            self.rfcontext.actions.unpress()

            self.rfcontext.undo_push('select smart')
            selectable_edges = [e for e in self.rfcontext.visible_edges() if len(e.link_faces) < 2]
            edge,_ = self.rfcontext.nearest2D_edge(edges=selectable_edges, max_dist=10)
            if not edge: return
            #self.rfcontext.select_inner_edge_loop(edge, supparts=False, only=sel_only)
            self.rfcontext.select_edge_loop(edge, supparts=False, only=sel_only)

        if self.rfcontext.actions.pressed('grab'):
            self.move_done_pressed = 'confirm'
            self.move_done_released = None
            self.move_cancelled = 'cancel'
            return 'move'

        if self.rfcontext.actions.pressed('increase count') and self.replay:
            # print('increase count')
            if self.strip_crosses is not None and not self.strip_edges:
                self.strip_crosses += 1
                self.replay()
            elif self.strip_loops is not None:
                self.strip_loops += 1
                self.replay()

        if self.rfcontext.actions.pressed('decrease count') and self.replay:
            # print('decrease count')
            if self.strip_crosses is not None and self.strip_crosses > 1 and not self.strip_edges:
                self.strip_crosses -= 1
                self.replay()
            elif self.strip_loops is not None and self.strip_loops > 1:
                self.strip_loops -= 1
                self.replay()

    @RFWidget.on_actioning('Autofill stroke')
    def stroking(self):
        hovering_sel_vert_snap,_ = self.rfcontext.accel_nearest2D_vert(max_dist=options['strokes snap dist'])
        if options['strokes snap stroke'] and hovering_sel_vert_snap:
            self.connection_post = (
                self.rfcontext.Point_to_Point2D(hovering_sel_vert_snap.co),
                self.actions.mouse,
            )
        else:
            self.connection_post = None

    @RFWidget.on_action('Autofill stroke')
    def stroke(self):
        # called when artist finishes a stroke

        Point_to_Point2D        = self.rfcontext.Point_to_Point2D
        raycast_sources_Point2D = self.rfcontext.raycast_sources_Point2D
        accel_nearest2D_vert    = self.rfcontext.accel_nearest2D_vert

        # filter stroke down where each pt is at least 1px away to eliminate local wiggling
        radius = self.rfwidgets['brush'].radius
        stroke = self.rfwidgets['brush'].stroke2D
        stroke = process_stroke_filter(stroke)
        stroke = process_stroke_source(
            stroke,
            raycast_sources_Point2D,
            Point_to_Point2D=Point_to_Point2D,
            clamp_point_to_symmetry=self.rfcontext.clamp_point_to_symmetry,
        )
        stroke3D = [raycast_sources_Point2D(s)[0] for s in stroke]
        stroke3D = [s for s in stroke3D if s]

        # bail if there aren't enough stroke data points to work with
        if len(stroke3D) < 2: return

        sel_verts = self.rfcontext.get_selected_verts()
        sel_edges = self.rfcontext.get_selected_edges()
        s0, s1 = Point_to_Point2D(stroke3D[0]), Point_to_Point2D(stroke3D[-1])
        bmv0, _ = accel_nearest2D_vert(point=s0, max_dist=options['strokes snap dist']) # self.rfwidgets['brush'].radius)
        bmv1, _ = accel_nearest2D_vert(point=s1, max_dist=options['strokes snap dist']) # self.rfwidgets['brush'].radius)
        if not options['strokes snap stroke']:
            if bmv0 and not bmv0.select: bmv0 = None
            if bmv1 and not bmv1.select: bmv1 = None
        bmv0_sel = bmv0 and bmv0 in sel_verts
        bmv1_sel = bmv1 and bmv1 in sel_verts

        if bmv0:
            stroke3D = [bmv0.co] + stroke3D
        if bmv1:
            stroke3D = stroke3D + [bmv1.co]

        self.strip_stroke3D = stroke3D
        self.strip_crosses = None
        self.strip_loops = None
        self.strip_edges = False
        self.replay = None

        # is the stroke in a circle?  note: circle must have a large enough radius
        cyclic  = (stroke[0] - stroke[-1]).length < radius
        cyclic &= any((s - stroke[0]).length > 2.0 * radius for s in stroke)

        if cyclic:
            self.replay = self.create_cycle # TODO: make sure the "circle" doesn't have too many sides, or else we can't autofill it
        else:
            self.replay = self.create_strip

        if self.replay: self.replay()


    def get_edges_for_extrude(self, only_closest=None):
        edges = { e for e in self.rfcontext.get_selected_edges() if e.is_boundary or e.is_wire }
        if not only_closest:
            return edges
        # TODO: find vert-connected-edge-island that has the edge closest to stroke
        return edges

    @RFTool.dirty_when_done
    def create_cycle(self):
        Point_to_Point2D = self.rfcontext.Point_to_Point2D
        stroke = [Point_to_Point2D(s) for s in self.strip_stroke3D]
        stroke += stroke[:1]
        if not all(stroke): return  # part of stroke cannot project

        if self.strip_crosses is not None:
            self.rfcontext.undo_repush('create cycle')
        else:
            self.rfcontext.undo_push('create cycle')

        if self.strip_crosses is None:
            stroke_len = sum((s1 - s0).length for (s0, s1) in iter_pairs(stroke, wrap=False))
            self.strip_crosses = max(1, math.ceil(stroke_len / (2 * self.rfwidgets['brush'].radius)))
        crosses = self.strip_crosses
        percentages = [i / crosses for i in range(crosses)]
        nstroke = restroke(stroke, percentages)

        if len(nstroke) <= 2:
            # too few vertices for a cycle
            self.rfcontext.alert_user(
                'Could not find create cycle from stroke.  Please try again.'
            )
            return

        with self.defer_recomputing_while():
            verts = [self.rfcontext.new2D_vert_point(s) for s in nstroke]
            edges = [self.rfcontext.new_edge([v0, v1]) for (v0, v1) in iter_pairs(verts, wrap=True)]
            self.patches.add_side(Side(edges))
            self.rfcontext.select(edges)
            self.just_created = True

    @RFTool.dirty_when_done
    def create_strip(self):
        Point_to_Point2D = self.rfcontext.Point_to_Point2D
        stroke = [Point_to_Point2D(s) for s in self.strip_stroke3D]
        if not all(stroke): return  # part of stroke cannot project

        if self.strip_crosses is not None:
            self.rfcontext.undo_repush('create strip')
        else:
            self.rfcontext.undo_push('create strip')

        self.rfcontext.get_vis_accel(force=True)

        if self.strip_crosses is None:
            stroke_len = sum((s1 - s0).length for (s0, s1) in iter_pairs(stroke, wrap=False))
            self.strip_crosses = max(1, math.ceil(stroke_len / (2 * self.rfwidgets['brush'].radius)))
        crosses = self.strip_crosses
        percentages = [i / crosses for i in range(crosses+1)]
        nstroke = restroke(stroke, percentages)

        if len(nstroke) < 2: return  # too few stroke points, from a short stroke?

        snap0,_ = self.rfcontext.accel_nearest2D_vert(point=nstroke[0],  max_dist=options['strokes merge dist']) # self.rfwidgets['brush'].radius)
        snap1,_ = self.rfcontext.accel_nearest2D_vert(point=nstroke[-1], max_dist=options['strokes merge dist']) # self.rfwidgets['brush'].radius)
        if not options['strokes snap stroke'] and snap0 and not snap0.select: snap0 = None
        if not options['strokes snap stroke'] and snap1 and not snap1.select: snap1 = None

        with self.defer_recomputing_while():
            verts = [self.rfcontext.new2D_vert_point(s) for s in nstroke]
            edges = [self.rfcontext.new_edge([v0, v1]) for (v0, v1) in iter_pairs(verts, wrap=False)]

            if snap0:
                co = snap0.co
                verts[0].merge(snap0)
                verts[0].co = co
                self.rfcontext.clean_duplicate_bmedges(verts[0])
            if snap1:
                co = snap1.co
                verts[-1].merge(snap1)
                verts[-1].co = co
                self.rfcontext.clean_duplicate_bmedges(verts[-1])

            self.patches.add_side(Side(edges))
            # for visual testing
            if self.patches.patches:
                e = []
                for side in self.patches.patches[-1].sides:
                    e += side.edges
                self.rfcontext.select(e) 

            #self.rfcontext.select(edges)
            self.just_created = True

    def mergeSnapped(self):
        """ Merging colocated visible verts """

        if not options['strokes automerge']: return

        # TODO: remove colocated faces
        if self.mousedown is None: return
        delta = Vec2D(self.actions.mouse - self.mousedown)
        set2D_vert = self.rfcontext.set2D_vert
        update_verts = []
        merge_dist = self.rfcontext.drawing.scale(options['strokes merge dist'])
        for bmv,xy in self.bmverts:
            if not xy: continue
            xy_updated = xy + delta
            for bmv1,xy1 in self.vis_bmverts:
                if not xy1: continue
                if bmv1 == bmv: continue
                if not bmv1.is_valid: continue
                d = (xy_updated - xy1).length
                if (xy_updated - xy1).length > merge_dist:
                    continue
                bmv1.merge_robust(bmv)
                self.rfcontext.select(bmv1)
                update_verts += [bmv1]
                break
        if update_verts:
            self.rfcontext.update_verts_faces(update_verts)
            #self.set_next_state()

    @FSM.on_state('move', 'enter')
    def move_enter(self):
        self.rfcontext.undo_push('move grabbed')

        self.move_opts = {
            'vis_accel': self.rfcontext.get_custom_vis_accel(
                selection_only=False,
                include_edges=False,
                include_faces=False,
            ),
        }

        sel_verts = self.rfcontext.get_selected_verts()
        vis_accel = self.rfcontext.get_vis_accel()
        vis_verts = self.rfcontext.accel_vis_verts
        Point_to_Point2D = self.rfcontext.Point_to_Point2D

        bmverts = [(bmv, Point_to_Point2D(bmv.co)) for bmv in sel_verts]
        self.bmverts = [(bmv, co) for (bmv, co) in bmverts if co]
        self.vis_bmverts = [(bmv, Point_to_Point2D(bmv.co)) for bmv in vis_verts if bmv.is_valid and bmv not in sel_verts]
        self.mousedown = self.rfcontext.actions.mouse
        self.defer_recomputing = True
        self.rfcontext.split_target_visualization_selected()
        self.rfcontext.set_accel_defer(True)
        self._timer = self.actions.start_timer(120)

        if options['hide cursor on tweak']: self.set_widget('hidden')

    @FSM.on_state('move')
    @RFTool.dirty_when_done
    @profiler.function
    def move(self):
        released = self.rfcontext.actions.released
        if self.actions.pressed(self.move_done_pressed):
            self.defer_recomputing = False
            self.mergeSnapped()
            return 'main'
        if self.actions.released(self.move_done_released):
            self.defer_recomputing = False
            self.mergeSnapped()
            return 'main'
        if self.actions.pressed('cancel'):
            self.defer_recomputing = False
            self.rfcontext.undo_cancel()
            return 'main'

        # only update verts on timer events and when mouse has moved
        #if not self.rfcontext.actions.timer: return
        #if self.actions.mouse_prev == self.actions.mouse: return
        if not self.actions.mousemove_stop: return

        delta = Vec2D(self.rfcontext.actions.mouse - self.mousedown)
        set2D_vert = self.rfcontext.set2D_vert
        for bmv,xy in self.bmverts:
            xy_updated = xy + delta
            # check if xy_updated is "close" to any visible verts (in image plane)
            # if so, snap xy_updated to vert position (in image plane)
            if options['polypen automerge']:
                bmv1,d = self.rfcontext.accel_nearest2D_vert(point=xy_updated, vis_accel=self.move_opts['vis_accel'], max_dist=options['strokes merge dist'])
                if bmv1 is None:
                    set2D_vert(bmv, xy_updated)
                    continue
                xy1 = self.rfcontext.Point_to_Point2D(bmv1.co)
                if not xy1:
                    set2D_vert(bmv, xy_updated)
                    continue
                set2D_vert(bmv, xy1)
            else:
                set2D_vert(bmv, xy_updated)
        self.rfcontext.update_verts_faces(v for v,_ in self.bmverts)

    @FSM.on_state('move', 'exit')
    def move_exit(self):
        self._timer.done()
        self.rfcontext.set_accel_defer(False)
        self.rfcontext.clear_split_target_visualization()

    @DrawCallbacks.on_draw('post2d')
    def draw_postpixel(self):
        if self._fsm.state == 'move': return
        bgl.glEnable(bgl.GL_BLEND)
        point_to_point2d = self.rfcontext.Point_to_Point2D
        up = self.rfcontext.Vec_up()
        size_to_size2D = self.rfcontext.size_to_size2D
        text_draw2D = self.rfcontext.drawing.text_draw2D
        self.rfcontext.drawing.set_font_size(12)

        for collection in self.edge_collections:
            l = len(collection['edges'])
            c = collection['center']
            xy = point_to_point2d(c)
            if not xy: continue
            xy.y += 10
            text_draw2D(str(l), xy, color=(1,1,0,1), dropshadow=(0,0,0,0.5))

        if self.connection_pre:
            Globals.drawing.draw2D_linestrip(self.connection_pre, themes['stroke'], width=2, stipple=[4,4])
        if self.connection_post:
            Globals.drawing.draw2D_linestrip(self.connection_post, themes['stroke'], width=2, stipple=[4,4])

