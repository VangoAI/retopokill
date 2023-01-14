'''
Copyright (C) 2021 CG Cookie
http://cgcookie.com
hello@cgcookie.com

Created by Jonathan Denning, Jonathan Williamson

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

import bpy
import math
from mathutils import Vector, Matrix
from mathutils.geometry import intersect_line_line_2d
from ...addon_common.common.debug  import dprint
from ...addon_common.common.maths  import Point,Point2D,Vec2D,Vec, Normal, clamp
from ...addon_common.common.bezier import CubicBezierSpline, CubicBezier
from ...addon_common.common.utils  import iter_pairs
import collections
import requests

class ExpandedPattern:
    def __init__(self, faces: list[list[int, int, int, int]], verts: list[tuple[float, float, float]], sides: list[list[int]]):
        self.faces = faces
        self.verts = verts
        self.sides = sides
        self.drawn_verts = [] # no need to keep track of edges
        self.drawn_faces = []
    
    def draw(self, rfcontext, patch: 'AutofillPatch'):
        assert not self.drawn_verts and not self.drawn_faces

        for i in range(len(self.verts)):
            for j in range(len(self.sides)):
                for k in range(len(self.sides[j])):
                    if i == self.sides[j][k]:
                        self.drawn_verts.append(patch.sides[j].verts[k])
                        break
                else:
                    continue
                break
            else:
                self.drawn_verts.append(rfcontext.new_vert_point(Point(self.verts[i])))
        self.drawn_faces = [rfcontext.new_face([self.drawn_verts[i] for i in face]) for face in self.faces]

    def destroy(self, rfcontext):
        if not self.drawn_verts and not self.drawn_faces:
            return
        for face in self.drawn_faces:
            try: # some faces will already be deleted if side subdivisions were changed
                rfcontext.delete_faces([face], del_empty_edges=True, del_empty_verts=False)
            except Exception as e:
                pass
        
        for i in range(len(self.verts)):
            for j in range(len(self.sides)):
                for k in range(len(self.sides[j])):
                    if i == self.sides[j][k]:
                        break
                else:
                    continue
                break
            else:
                rfcontext.delete_verts([self.drawn_verts[i]]) # deletes the faces too
        self.drawn_verts = []
        self.drawn_faces = []

    def select(self, rfcontext):
        rfcontext.select(self.drawn_faces)

    def contains_face(self, face):
        return face in self.drawn_faces

    @staticmethod
    def none():
        return ExpandedPattern([], [], [])

class Side:
    def __init__(self):
        self.verts = []

    @staticmethod
    def from_edges(edges: list):
        side = Side()
        side.verts = [edge.verts[0] for edge in edges] + [edges[-1].verts[1]]
        return side
    
    @staticmethod
    def from_verts(verts: list):
        side = Side()
        side.verts = verts
        return side

    @staticmethod
    def multiple_from_edges(edges: set):
        '''
        turn an unordered set of edges into (possible multiple) sides with ordered verts
        '''
        sides = [Side.from_edges([edge]) for edge in edges]
        while len(sides) > 1:
            for i in range(len(sides)):
                for j in range(i + 1, len(sides)):
                    if sides[i].shares_endpoint_with(sides[j]):
                        sides[i].merge(sides[j])
                        sides.pop(j)
                        break
                else:
                    continue
                break
            else:
                break
        return sides

    def change_subdivisions(self, rfcontext, change_by: int):
        if len(self.verts) + change_by < 2:
            return
        points = [v.co for v in self.verts]
        percentages = [i / (len(self.verts) + change_by - 1) for i in range(len(self.verts) + change_by)]
        new_points = restroke(points, percentages)
        new_verts = [self.verts[0]] + [rfcontext.new_vert_point(p) for p in new_points[1:-1]] + [self.verts[-1]]
        try: # edge case where its only the endpoints--the edge wont be deleted, so there will be a duplicate edge error if it happens again
            edges = [rfcontext.new_edge([v0, v1]) for (v0, v1) in iter_pairs(new_verts, wrap=False)]
            rfcontext.select(edges, only=False)
        except Exception as e:
            pass
        rfcontext.delete_verts(self.verts[1:-1])
        self.verts = new_verts

    def shares_endpoint_with(self, other):
        return self.verts[0] == other.verts[0] or self.verts[0] == other.verts[-1] or self.verts[-1] == other.verts[0] or self.verts[-1] == other.verts[-1]

    def merge(self, other):
        if self.verts[0] == other.verts[0]:
            self.verts = other.verts[::-1] + self.verts[1:]
        elif self.verts[0] == other.verts[-1]:
            self.verts = other.verts + self.verts[1:]
        elif self.verts[-1] == other.verts[0]:
            self.verts = self.verts[:-1] + other.verts
        elif self.verts[-1] == other.verts[-1]:
            self.verts = self.verts[:-1] + other.verts[::-1]
        else:
            assert False, 'sides do not share an endpoint'

    def __eq__(self, other):
        return set(self.verts) == set(other.verts) # to account for reversed sides

class AutofillPatch:
    def __init__(self, sides: list[Side], rfcontext):
        '''
        takes a list of Side objects, not necessarily in CCW order.
        '''
        def order_sides(sides: list[Side]) -> list[Side]:
            '''
            orders the sides in CCW order
            '''
            ordered_sides = []
            curr = sides[0] # assumes first side is already CCW--change later
            sides.remove(curr)
            while True:
                ordered_sides.append(curr)
                for i in range(len(sides)):
                    if curr.verts[-1] == sides[i].verts[0]:
                        curr = sides[i]
                        sides.remove(curr)
                        break
                    elif curr.verts[-1] == sides[i].verts[-1]:
                        curr = sides[i]
                        curr.verts = curr.verts[::-1]
                        sides.remove(curr)
                        break
                else:
                    assert False # should never happen
                if curr.verts[-1] == ordered_sides[0].verts[0]:
                    ordered_sides.append(curr)
                    break
            return ordered_sides

        self.rfcontext = rfcontext
        self.sides = order_sides(sides)
        self.expanded_patterns = []
        self.i = -1
        self.load()

    def next(self):
        self.change(1)

    def prev(self):
        self.change(-1)

    def change(self, x):
        self.expanded_patterns[self.i].destroy(self.rfcontext)
        self.i = min(max(0, self.i + x), len(self.expanded_patterns) - 1)
        self.expanded_patterns[self.i].draw(self.rfcontext, self)
        self.select()

    def select(self):
        self.expanded_patterns[self.i].select(self.rfcontext)

    def contains_face(self, face):
        if self.i == -1:
            return False
        return self.expanded_patterns[self.i].contains_face(face)

    def load(self):
        '''
        loads the expanded patterns
        '''
        def to_json() -> list[list[tuple[float, float, float]]]:
            sides = []
            for side in self.sides:
                sides.append([(v.co.x, v.co.y, v.co.z) for v in side.verts])
            return sides

        if self.i != -1:
            self.expanded_patterns[self.i].destroy(self.rfcontext)

        r = requests.post("http://127.0.0.1:5000/get_expanded_patterns", json=to_json())
        self.expanded_patterns = [ExpandedPattern(p['faces'], p['verts'], p['sides']) for p in r.json()]
        self.i = -1
        if self.expanded_patterns:
            self.next()

class AutofillPatches:
    def __init__(self, rfcontext):
        self.rfcontext = rfcontext
        self.patches: list[AutofillPatch] = []
        self.selected_patch_index = -1
        self.current_sides: list[Side] = []

    def add_side(self, side):
        self.current_sides.append(side)
        if len(self.current_sides) == 4:
            total = sum([len(side.verts) for side in self.current_sides])
            if total % 2  == 1:
                self.current_sides[-1].change_subdivisions(self.rfcontext, 1)
            patch = AutofillPatch(self.current_sides, self.rfcontext)
            self.current_sides = []
            self.patches.append(patch)
            self.selected_patch_index = len(self.patches) - 1

    def change_subdivisions(self, sides: list[Side], add: bool):
        assert len(sides) == 1 or len(sides) == 2

        for side in self.current_sides:
            if sides[0] == side:
                assert len(sides) == 1
                side.change_subdivisions(self.rfcontext, 1 if add else -1)
                return

        for patch in self.patches:
            if sides[0] in patch.sides:
                if len(sides) == 1:
                    num_to_add = 2 if add else -2
                    patch.sides[patch.sides.index(sides[0])].change_subdivisions(self.rfcontext, num_to_add)
                else:
                    if sides[1] in patch.sides:
                        patch.sides[patch.sides.index(sides[0])].change_subdivisions(self.rfcontext, 1 if add else -1)
                        patch.sides[patch.sides.index(sides[1])].change_subdivisions(self.rfcontext, 1 if add else -1)
                    else:
                        return # sides are not part of same patch
                patch.load()
                return

    def select_patch_from_face(self, face):
        '''
        select the patch containing the face
        '''
        for i, patch in enumerate(self.patches):
            if patch.contains_face(face):
                if self.selected_patch_index == i:
                    self.deselect()
                else:
                    patch.select()
                    self.selected_patch_index = i
                return
        self.deselect()

    def deselect(self):
        self.rfcontext.deselect_all()
        self.selected_patch_index = -1

    def is_patch_selected(self):
        return self.selected_patch_index != -1

    def next(self):
        assert self.is_patch_selected()
        self.patches[self.selected_patch_index].next()

    def prev(self):
        assert self.is_patch_selected()
        self.patches[self.selected_patch_index].prev()

def process_stroke_filter(stroke, min_distance=1.0, max_distance=2.0):
    ''' filter stroke to pts that are at least min_distance apart '''
    nstroke = stroke[:1]
    for p in stroke[1:]:
        v = p - nstroke[-1]
        l = v.length
        if l < min_distance: continue
        d = v / l
        while l > 0:
            q = nstroke[-1] + d * min(l, max_distance)
            nstroke.append(q)
            l -= max_distance
    return nstroke

def process_stroke_source(stroke, raycast, Point_to_Point2D=None, is_point_on_mirrored_side=None, mirror_point=None, clamp_point_to_symmetry=None):
    ''' filter out pts that don't hit source on non-mirrored side '''
    pts = [(pt, raycast(pt)[0]) for pt in stroke]
    pts = [(pt, p3d) for (pt, p3d) in pts if p3d]
    if Point_to_Point2D and mirror_point:
        pts_ = [Point_to_Point2D(mirror_point(p3d)) for (_, p3d) in pts]
        pts = [(pt, raycast(pt)[0]) for pt in pts_]
        pts = [(pt, p3d) for (pt, p3d) in pts if p3d]
    if Point_to_Point2D and clamp_point_to_symmetry:
        pts_ = [Point_to_Point2D(clamp_point_to_symmetry(p3d)) for (_, p3d) in pts]
        pts = [(pt, raycast(pt)[0]) for pt in pts_]
        pts = [(pt, p3d) for (pt, p3d) in pts if p3d]
    if is_point_on_mirrored_side:
        pts = [(pt, p3d) for (pt, p3d) in pts if not is_point_on_mirrored_side(p3d)]
    return [pt for (pt, _) in pts]

def find_edge_cycles(edges):
    edges = set(edges)
    verts = {v: set() for e in edges for v in e.verts}
    for e in edges:
        for v in e.verts:
            verts[v].add(e)
    in_cycle = set()
    for vstart in verts:
        if vstart in in_cycle: continue
        for estart in vstart.link_edges:
            if estart not in edges: continue
            if estart in in_cycle: continue
            q = [(estart, vstart, None)]
            found = None
            trace = {}
            while q:
                ec, vc, ep = q.pop(0)
                if ec in trace: continue
                trace[ec] = (vc, ep)
                vn = ec.other_vert(vc)
                if vn == vstart:
                    found = ec
                    break
                q += [(en, vn, ec) for en in vn.link_edges if en in edges]
            if not found: continue
            l = [found]
            in_cycle.add(found)
            while True:
                vn, ep = trace[l[-1]]
                in_cycle.add(vn)
                in_cycle.add(ep)
                if vn == vstart: break
                l.append(ep)
            yield l

def find_edge_strips(edges):
    ''' find edge strips '''
    edges = set(edges)
    verts = {v: set() for e in edges for v in e.verts}
    for e in edges:
        for v in e.verts:
            verts[v].add(e)
    ends = [v for v in verts if len(verts[v]) == 1]
    def get_edge_sequence(v0, v1):
        trace = {}
        q = [(None, v0)]
        while q:
            vf,vt = q.pop(0)
            if vt in trace: continue
            trace[vt] = vf
            if vt == v1: break
            for e in verts[vt]:
                q.append((vt, e.other_vert(vt)))
        if v1 not in trace: return []
        l = []
        while v1 is not None:
            l.append(v1)
            v1 = trace[v1]
        l.reverse()
        return [v0.shared_edge(v1) for (v0, v1) in iter_pairs(l, wrap=False)]
    for i0 in range(len(ends)):
        for i1 in range(i0+1,len(ends)):
            l = get_edge_sequence(ends[i0], ends[i1])
            if l: yield l

def get_strip_verts(edge_strip):
    l = len(edge_strip)
    if l == 0: return []
    if l == 1:
        e = edge_strip[0]
        return list(e.verts) if e.is_valid else []
    vs = []
    for e0, e1 in iter_pairs(edge_strip, wrap=False):
        vs.append(e0.shared_vert(e1))
    vs = [edge_strip[0].other_vert(vs[0])] + vs + [edge_strip[-1].other_vert(vs[-1])]
    return vs


def restroke(stroke, percentages):
    lens = [(s0 - s1).length for (s0, s1) in iter_pairs(stroke, wrap=False)]
    total_len = sum(lens)
    stops = [max(0, min(1, p)) * total_len for p in percentages]
    dist = 0
    istroke = 0
    istop = 0
    nstroke = []
    while istroke + 1 < len(stroke) and istop < len(stops):
        if lens[istroke] <= 0:
            istroke += 1
            continue
        t = (stops[istop] - dist) / lens[istroke]
        if t < 0:
            istop += 1
        elif t > 1.000001:
            dist += lens[istroke]
            istroke += 1
        else:
            s0, s1 = stroke[istroke], stroke[istroke + 1]
            nstroke.append(s0 + (s1 - s0) * t)
            istop += 1
    return nstroke

def walk_to_corner(from_vert, to_edges):
    to_verts = {v for e in to_edges for v in e.verts}
    edges = [
        (e, from_vert, None)
        for e in from_vert.link_edges
        if not e.is_manifold and e.is_valid
    ]
    touched = {}
    found = None
    while edges:
        ec, v0, ep = edges.pop(0)
        if ec in touched: continue
        touched[ec] = (v0, ep)
        v1 = ec.other_vert(v0)
        if v1 in to_verts:
            found = ec
            break
        nedges = [
            (en, v1, ec)
            for en in v1.link_edges
            if en != ec and not en.is_manifold and en.is_valid
        ]
        edges += nedges
    if not found: return None
    # walk back
    walk = [found]
    while True:
        ec = walk[-1]
        v0, ep = touched[ec]
        if v0 == from_vert:
            break
        walk.append(ep)
    return walk
