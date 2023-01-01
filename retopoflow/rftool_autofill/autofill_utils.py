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

class Side:
    def __init__(self, edges):
        self.edges = edges

    def get_endpoints(self):
        return self.edges[0].verts[0], self.edges[-1].verts[1]

    def index(self, vert):
        '''
        return index of the edge that BEGINS with the vert else -1
        '''
        for i, edge in enumerate(self.edges):
            if edge.verts[0] == vert:
                return i
        return -1
    
    def contains(self, vert):
        '''
        check if side contains vert. EXCLUDES THE SECOND VERT OF THE FINAL EDGE!
        '''
        return self.index(vert) >= 0

    def contains_full(self, vert):
        '''
        check if side contains vert. INCLUDES THE SECOND VERT OF THE FINAL EDGE!
        '''
        return self.index(vert) >= 0 or self.edges[-1].verts[1] == vert

class AutofillPatch:
    def __init__(self):
        self.sides = []
        self.closed = False

    def count_shared_endpoints(self, endpoint):
        count = 0
        for side in self.sides:
            start, end = side.get_endpoints()
            if start == endpoint: # seperate these two cases to account for a patch being a single side that is a circle.
                count += 1
            if end == endpoint:
                count += 1
        return count

    def compute_closed(self):
        for side in self.sides:
            start, end = side.get_endpoints()
            if self.count_shared_endpoints(start) != 2 or self.count_shared_endpoints(end) != 2:
                return False
        return True

    def can_add_side(self, side):
        '''
        check if side can be added to patch
        '''
        if self.closed:
            return False
        if not self.sides:
            return True
        start, end = side.get_endpoints()
        start_count, end_count = self.count_shared_endpoints(start), self.count_shared_endpoints(end)
        print(start_count, end_count)
        if start_count >= 2 or end_count >= 2:
            return False
        return start_count == 1 or end_count == 1

    def add_side(self, side):
        '''
        add side to patch
        '''
        assert self.can_add_side(side), 'cannot add side to patch'
        self.sides.append(side)
        if self.compute_closed():
            self.closed = True

    def is_split_by(self, side):
        '''
        check if side splits this patch in half
        '''
        if not self.closed:
            return False
        start, end = side.get_endpoints()
        return any(s.contains(start) for s in self.sides) and any(s.contains(end) for s in self.sides)

    def split(self, side):
        '''
        split this patch in half; returns the two new patches
        '''
        assert self.is_split_by(side), 'side does not split patch'

        def get_sides(vert):
            side_index = self.index(vert)
            assert side_index >= 0, 'could not find side containing start'
            edge_index = self.sides[side_index].index(vert)
            if edge_index == 0:
                # case 3a: split from start of side
                side1 = self.sides[side_index]
                side2 = self.sides[(side_index - 1) % len(self.sides)]
                return side1, side2
            else:
                # case 3b: split from middle of side
                side1 = Side(self.sides[side_index].edges[edge_index:])
                side2 = Side(self.sides[side_index].edges[:edge_index])
                return side1, side2
            # case 3c: split from end of side (not possible because of how index works)
        
        def create_patch_half(start_side, end_side):
            new_patch = AutofillPatch()
            new_patch.add_side(side)
            if new_patch.can_add_side(start_side):
                new_patch.add_side(start_side)
            else:
                return None
            if new_patch.can_add_side(end_side):
                new_patch.add_side(end_side)
            else:
                return None
                
            while not new_patch.closed:
                for s in self.sides:
                    if not s.contains_full(start) and not s.contains_full(end) and new_patch.can_add_side(s): # can't use the sides that cross the split
                        new_patch.add_side(s)
                        break
                else:
                    return None
            return new_patch

        start, end = side.get_endpoints()
        start_side1, start_side2 = get_sides(start)
        end_side1, end_side2 = get_sides(end)
        p1, p2 = create_patch_half(start_side1, end_side1), create_patch_half(start_side2, end_side2)
        if p1 and p2:
            return p1, p2
        return create_patch_half(start_side1, end_side2), create_patch_half(start_side2, end_side1)

    def index(self, vert):
        '''
        return index of the side that contains the vert else -1.
        if the vert is shared by two sides, return the index of the side that STARTS with the vert
        '''
        for i in range(len(self.sides)):
            if self.sides[i].contains(vert):
                return i
        return -1

class AutofillPatches:
    def __init__(self):
        self.patches = []

    def add_side(self, side):
        '''
        Need to take care of 3 cases:
        1. side is start of a new patch, and may start from a side of a closed patch
        2. side is continuation of existing patch, and may close the patch
        3. side splits a closed patch in half
        '''
        for patch in self.patches:
            # case 2
            if patch.can_add_side(side):
                patch.add_side(side)
                print("case 2", patch.closed)
                return
            # case 3
            if patch.closed and patch.is_split_by(side):
                self.patches.remove(patch)
                p1, p2 = patch.split(side)
                self.patches.append(p1)
                self.patches.append(p2)
                print("case 3")
                return
        # case 1
        patch = AutofillPatch()
        patch.add_side(side)
        self.patches.append(patch)
        print("case 1")

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
