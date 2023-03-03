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

from ...addon_common.common.maths  import Point
from ...addon_common.common.utils  import iter_pairs
from ..rftool_strokes.strokes_utils import restroke
from ...config.options import options
from ..rf.rf_api import RetopoFlow_API
import copy

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

    def save(self):
        return {
            'faces': copy.deepcopy(self.faces),
            'verts': copy.deepcopy(self.verts),
            'sides': copy.deepcopy(self.sides),
            'drawn_verts': [(v.co.x, v.co.y, v.co.z) for v in self.drawn_verts],
        }

    @staticmethod
    def from_saved(expanded_pattern, rfcontext):
        ep = ExpandedPattern(expanded_pattern['faces'], expanded_pattern['verts'], expanded_pattern['sides'])
        if expanded_pattern['drawn_verts']:
            drawn_verts = []
            for p in expanded_pattern['drawn_verts']:
                vert, dist = rfcontext.nearest_vert_point(Point(p))
                if dist == 0:
                    drawn_verts.append(vert)
            ep.drawn_verts = drawn_verts

            drawn_faces = []
            for face in ep.faces:
                face, dist = rfcontext.nearest2D_face(Point.average([drawn_verts[v_i].co for v_i in face]))
                if dist == 0:
                    drawn_faces.append(face)
            ep.drawn_faces = drawn_faces
        return ep

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

    @staticmethod
    def order(sides: list['Side']) -> tuple[list['Side'], bool]:
        '''
        if the sides form a patch, return the sides in CW/CCW order.
        if the sides don't form a patch, return an arbitrary ordering.
        also returns if the sides form a patch or not.
        '''
        sides = sides[:]

        ordered_sides = []
        curr = sides[0]
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
                return ordered_sides + sides, False # does not form a patch
            if curr.verts[-1] == ordered_sides[0].verts[0]:
                ordered_sides.append(curr)
                break
        return ordered_sides, True

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

    def save(self):
        return {
            'points': [(v.co.x, v.co.y, v.co.z) for v in self.verts]
        }

    @staticmethod
    def from_saved(side, rfcontext):
        verts = []
        for p in side['points']:
            vert, dist = rfcontext.nearest_vert_point(Point(p))
            if dist == 0:
                verts.append(vert)
        return Side.from_verts(verts)

    def __eq__(self, other):
        return set(self.verts) == set(other.verts) # to account for reversed sides

class AutofillPatch:
    def __init__(self, sides: list[Side] = None, rfcontext=None):
        '''
        takes a list of Side objects
        must be in either CW or CCW order.
        '''
        self.rfcontext = rfcontext
        self.sides = sides
        self.expanded_patterns = []
        self.i = -1
        self.load()

    def next(self):
        '''
        returns whether the next expanded pattern was succesfully loaded
        '''
        if self.i + 1 < len(self.expanded_patterns):
            self.change(1)
            return True
        return False

    def prev(self):
        '''
        returns whether the previous expanded pattern was succesfully loaded
        '''
        if self.i - 1 >= 0:
            self.change(-1)
            return True
        return False

    def change(self, x: int):
        self.expanded_patterns[self.i].destroy(self.rfcontext)
        self.i = self.i + x
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

        if not self.sides:
            return

        if self.i != -1:
            self.expanded_patterns[self.i].destroy(self.rfcontext)

        r = RetopoFlow_API.post('/get_expanded_patterns', to_json())
        self.expanded_patterns = [ExpandedPattern(p['faces'], p['verts'], p['sides']) for p in r.json()]
        self.i = -1
        if self.expanded_patterns:
            self.next()

    def save(self):
        return {
            'sides': [side.save() for side in self.sides],
            'expanded_patterns': [expanded_pattern.save() for expanded_pattern in self.expanded_patterns],
            'i': self.i,
        }

    @staticmethod
    def from_saved(patch_saved: dict, rfcontext):
        patch = AutofillPatch(rfcontext=rfcontext)
        patch.sides = [Side.from_saved(side, rfcontext) for side in patch_saved['sides']]
        patch.expanded_patterns = [ExpandedPattern.from_saved(expanded_pattern, rfcontext) for expanded_pattern in patch_saved['expanded_patterns']]
        patch.i = patch_saved['i']
        return patch

class AutofillPatches:
    def __init__(self, rfcontext):
        self.rfcontext = rfcontext
        self.last_patch = None
        self.is_patch_selected = False
        self.current_sides: list[Side] = []

    def add_side(self, side):
        self.current_sides.append(side)
        self.current_sides, forms_patch = Side.order(self.current_sides)
        if forms_patch:
            total = sum([len(side.verts) - 1 for side in self.current_sides])
            if total % 2  == 1:
                for side in self.current_sides:
                    first_edge = side.verts[0].shared_edge(side.verts[1])
                    if not first_edge.link_faces:
                        side.change_subdivisions(self.rfcontext, 1)
                        break
            patch = AutofillPatch(self.current_sides, self.rfcontext)
            self.current_sides = []
            self.last_patch = patch
            self.is_patch_selected = True

    def change_subdivisions(self, sides: list[Side], add: bool) -> bool:
        '''
        returns whether a side's subdivision was succesfully changed
        '''
        assert len(sides) == 1 or len(sides) == 2

        for side in self.current_sides:
            if sides[0] == side:
                assert len(sides) == 1
                side.change_subdivisions(self.rfcontext, 1 if add else -1)
                return True

        if sides[0] in self.last_patch.sides:
            if len(sides) == 1:
                num_to_add = 2 if add else -2
                self.last_patch.sides[self.last_patch.sides.index(sides[0])].change_subdivisions(self.rfcontext, num_to_add)
            else:
                if sides[1] in self.last_patch.sides:
                    self.last_patch.sides[self.last_patch.sides.index(sides[0])].change_subdivisions(self.rfcontext, 1 if add else -1)
                    self.last_patch.sides[self.last_patch.sides.index(sides[1])].change_subdivisions(self.rfcontext, 1 if add else -1)
                else:
                    return False # sides are not part of same patch
            self.last_patch.load()
            return True
        return False

    def select_patch_from_face(self, face):
        '''
        select the last patch if it contains the given face
        '''
        if self.is_patch_selected:
            self.deselect()
            self.is_patch_selected = False
        elif self.last_patch and self.last_patch.contains_face(face):
            self.last_patch.select()
            self.is_patch_selected = True

    def deselect(self):
        self.rfcontext.deselect_all()
        self.is_patch_selected = False

    def next(self) -> bool:
        assert self.is_patch_selected
        return self.last_patch.next()

    def prev(self) -> bool:
        assert self.is_patch_selected
        return self.last_patch.prev()

    def save(self):
        try:
            last_patch = self.last_patch.save()
        except Exception as e:
            last_patch = None

        current_sides = []
        for side in self.current_sides:
            try:
                current_sides.append(side.save())
            except Exception as e:
                pass

        return {
            'last_patch': last_patch,
            'current_sides': current_sides,
            'is_patch_selected': self.is_patch_selected,
        }

    @staticmethod
    def from_saved(patches_saved: dict, rfcontext):
        patches = AutofillPatches(rfcontext)
        patches.last_patch = AutofillPatch.from_saved(patches_saved['last_patch'], rfcontext) if patches_saved['last_patch'] else None
        patches.is_patch_selected = patches_saved['is_patch_selected']
        patches.current_sides = [Side.from_saved(side, rfcontext) for side in patches_saved['current_sides']]
        return patches

