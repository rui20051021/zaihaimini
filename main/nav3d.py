"""Continuous multi-floor evacuation prototype driven by calibrated scene navigation.

Coordinates use metres in a right-handed scene frame: X/Z are horizontal and Y is up.
Walkable polygons are the authoritative collision/navigation surface; the GLB is visual.
"""
from __future__ import annotations

import csv
import heapq
import io
import json
import math
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Vec2 = Tuple[float, float]  # x, z
Vec3 = Tuple[float, float, float]  # x, y, z
SCENE_SCHEMA = 2
DATA_SCHEMA = 2
MAX_SCENE_JSON_BYTES = 2 * 1024 * 1024
MAX_POLYGON_POINTS = 10000
MAX_SCENE_REGIONS = 500


class SceneValidationError(ValueError):
    pass


# Social-force repulsion in this approximation is unbounded, so a dense ring of
# agents can generate a push that exactly cancels forward motion and deadlocks
# the whole group.  Crowd pushes are therefore expressed as a displacement and
# capped to this fraction of the distance the agent is trying to walk, which
# guarantees net progress toward the target while still separating bodies.
CROWD_PUSH_FRACTION = 0.6
# The interaction radius used by _crowd_force is 1.2 m. A matching spatial
# cell size means only the 3x3x3 neighbouring cells can contain a neighbour in
# range; the old 0.9 m cells required scanning 5x5x5 bins for the same query.
# Body radius used for wall collision.  A person is only allowed where the
# whole circle fits inside the walkable polygon, which is what stops the
# capsule from visually intersecting facades when someone hugs a wall.
# Door clearance is validated separately by the calibration checks.
AGENT_RADIUS = 0.15
ELEVATION_TOLERANCE_M = 0.02
# Spatial cell used by the social-force query.  Values larger than the 1.2 m
# interaction radius would miss neighbours, so keep them equal.
CROWD_BIN_SIZE = 1.2


def _finite_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SceneValidationError(f"{label} 必须是有限数字")
    return float(value)


def _vec2(value, label: str) -> Vec2:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SceneValidationError(f"{label} 必须是 [x, z]")
    return (_finite_number(value[0], label), _finite_number(value[1], label))


def _vec3(value, label: str) -> Vec3:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SceneValidationError(f"{label} 必须是 [x, y, z]")
    return tuple(_finite_number(v, label) for v in value)  # type: ignore[return-value]


def _cross(a: Vec2, b: Vec2, c: Vec2) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Vec2, b: Vec2, p: Vec2, eps: float = 1e-8) -> bool:
    return (min(a[0], b[0]) - eps <= p[0] <= max(a[0], b[0]) + eps
            and min(a[1], b[1]) - eps <= p[1] <= max(a[1], b[1]) + eps
            and abs(_cross(a, b, p)) <= eps)


def _segments_intersect(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
    ab_c, ab_d, cd_a, cd_b = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    eps = 1e-9
    if ((ab_c > eps and ab_d < -eps) or (ab_c < -eps and ab_d > eps)) and \
       ((cd_a > eps and cd_b < -eps) or (cd_a < -eps and cd_b > eps)):
        return True
    return ((abs(ab_c) <= eps and _on_segment(a, b, c))
            or (abs(ab_d) <= eps and _on_segment(a, b, d))
            or (abs(cd_a) <= eps and _on_segment(c, d, a))
            or (abs(cd_b) <= eps and _on_segment(c, d, b)))


def _area(poly: Sequence[Vec2]) -> float:
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
               - poly[(i + 1) % len(poly)][0] * poly[i][1]
               for i in range(len(poly))) / 2.0


def _normalize_ring(raw, label: str) -> List[Vec2]:
    if not isinstance(raw, list) or not 3 <= len(raw) <= MAX_POLYGON_POINTS:
        raise SceneValidationError(f"{label} 至少需要 3 个点，最多 {MAX_POLYGON_POINTS} 个点")
    poly = [_vec2(p, label) for p in raw]
    if poly[0] == poly[-1]:
        poly.pop()
    if len(set(poly)) < 3 or abs(_area(poly)) < 1e-5:
        raise SceneValidationError(f"{label} 面积为零或点重复")
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if math.dist(a, b) < 1e-6:
            raise SceneValidationError(f"{label} 存在重复相邻点")
        for j in range(i + 1, n):
            if j in {i, (i + 1) % n} or i == (j + 1) % n:
                continue
            if _segments_intersect(a, b, poly[j], poly[(j + 1) % n]):
                raise SceneValidationError(f"{label} 不能自相交")
    if _area(poly) < 0:
        poly.reverse()
    return poly


def point_in_ring(p: Vec2, ring: Sequence[Vec2]) -> bool:
    inside = False
    x, z = p
    for i, a in enumerate(ring):
        b = ring[(i + 1) % len(ring)]
        if _on_segment(a, b, p, 1e-7):
            return True
        if (a[1] > z) != (b[1] > z):
            hit_x = (b[0] - a[0]) * (z - a[1]) / (b[1] - a[1]) + a[0]
            if x < hit_x:
                inside = not inside
    return inside


def point_in_region(p: Vec2, region: dict) -> bool:
    return (point_in_ring(p, region["outer"])
            and not any(point_in_ring(p, hole) for hole in region["holes"]))


def _point_segment_distance(p: Vec2, a: Vec2, b: Vec2) -> Tuple[float, Vec2]:
    dx, dz = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dz * dz
    t = 0.0 if denom <= 1e-15 else max(0.0, min(1.0, ((p[0]-a[0])*dx+(p[1]-a[1])*dz)/denom))
    q = (a[0] + t * dx, a[1] + t * dz)
    return math.dist(p, q), q


def closest_region_point(p: Vec2, region: dict, margin: float = 0.0) -> Vec2:
    """Project to walkable region. Margin keeps the agent radius inside boundaries."""
    if point_in_region(p, region) and (margin <= 0 or _boundary_distance(p, region) >= margin):
        return p
    rings = [region["outer"], *region["holes"]]
    candidates = []
    for ring in rings:
        for i, a in enumerate(ring):
            b = ring[(i + 1) % len(ring)]
            _, q = _point_segment_distance(p, a, b)
            # Nudge a candidate toward the outer ring's vertex-average interior.
            center = (sum(v[0] for v in region["outer"]) / len(region["outer"]),
                      sum(v[1] for v in region["outer"]) / len(region["outer"]))
            dx, dz = center[0] - q[0], center[1] - q[1]
            length = math.hypot(dx, dz) or 1.0
            q = (q[0] + dx / length * (margin + 1e-4), q[1] + dz / length * (margin + 1e-4))
            if point_in_region(q, region):
                candidates.append(q)
    if not candidates:
        candidates = [region["center"]]
    return min(candidates, key=lambda q: math.dist(p, q))


def region_clearance(p: Vec2, region: dict) -> float:
    """Signed-ish clearance: how far ``p`` may move before leaving the region.

    Returns the distance to the nearest boundary edge when ``p`` is inside the
    walkable area, and a negative value when it is outside the outer ring or
    inside a hole.  This is the authoritative test for a body of non-zero
    radius: a person is only "inside the room" while its whole circle fits.
    """
    sign = 0.0 if point_in_region(p, region) else -1.0
    best = math.inf
    for ring in [region["outer"], *region["holes"]]:
        for i, a in enumerate(ring):
            best = min(best, _point_segment_distance(p, a, ring[(i + 1) % len(ring)])[0])
    if best is math.inf:
        return sign
    return best if sign == 0.0 else -best


def _region_edges(region):
    return [(a, ring[(i + 1) % len(ring)])
            for ring in [region["outer"], *region["holes"]] for i, a in enumerate(ring)]


def sweep_disc(p: Vec2, delta: Vec2, edges, radius: float, iterations: int = 5) -> Vec2:
    """Continuous circle/segment collision, then slide along the actual wall normal.

    Test the whole sweep, not only its endpoint: thin obstacles and diagonal
    walls cannot be skipped. Existing penetrations may move out, never deeper.
    """
    limits = [(a, b, min(radius, _point_segment_distance(p, a, b)[0])) for a, b in edges]
    pos, remaining = p, delta
    for _ in range(iterations):
        dx, dz = remaining
        length2 = dx*dx + dz*dz
        if length2 < 1e-16: break
        hits = []
        for a, b, r in limits:
            ex, ez = b[0]-a[0], b[1]-a[1]
            length = math.hypot(ex, ez)
            if length < 1e-12: continue
            nx, nz = -ez/length, ex/length
            side = (pos[0]-a[0])*nx + (pos[1]-a[1])*nz
            speed = dx*nx + dz*nz
            for sign in (-1, 1):
                if speed*sign >= -1e-12: continue
                t = (sign*r-side)/speed
                if -1e-8 <= t <= 1:
                    qx, qz = pos[0]+dx*t, pos[1]+dz*t
                    u = ((qx-a[0])*ex+(qz-a[1])*ez)/(length*length)
                    if 0 <= u <= 1 and not (r<1e-7 and t<1e-8): hits.append((max(0.0,t),sign*nx,sign*nz))
            for end in (a,b):
                ox, oz = pos[0]-end[0], pos[1]-end[1]
                dot = ox*dx+oz*dz
                disc = dot*dot-length2*(ox*ox+oz*oz-r*r)
                if disc < 0: continue
                t = (-dot-math.sqrt(max(0.0,disc)))/length2
                if -1e-8 <= t <= 1:
                    hx,hz=ox+dx*t,oz+dz*t
                    hlen=math.hypot(hx,hz)
                    if hlen>1e-10 and dx*hx+dz*hz < -1e-12:
                        hits.append((max(0.0,t),hx/hlen,hz/hlen))
        if not hits:
            pos=(pos[0]+dx,pos[1]+dz);break
        t=min(h[0] for h in hits)
        safe=max(0.0,t-1e-7/math.sqrt(length2))
        pos=(pos[0]+dx*safe,pos[1]+dz*safe)
        remaining=(dx*(1-t),dz*(1-t))
        for ht,nx,nz in hits:
            if ht > t+1e-7: continue
            into=remaining[0]*nx+remaining[1]*nz
            if into < 0: remaining=(remaining[0]-into*nx,remaining[1]-into*nz)
    return pos


def slide_along_region(p: Vec2, delta: Vec2, region: dict, radius: float, iterations: int = 5) -> Vec2:
    result=sweep_disc(p,delta,_region_edges(region),max(1e-7,radius),iterations)
    return result if point_in_region(result,region) else p


def _subtract_polygon(a, b, polygon):
    """Split a wall, keeping only parts outside a portal / adjacent surface."""
    dx,dz=b[0]-a[0],b[1]-a[1];cuts=[0.0,1.0]
    for c,d in _region_edges(polygon):
        ex,ez=d[0]-c[0],d[1]-c[1];den=dx*ez-dz*ex
        if abs(den)<1e-12: continue
        t=((c[0]-a[0])*ez-(c[1]-a[1])*ex)/den
        u=((c[0]-a[0])*dz-(c[1]-a[1])*dx)/den
        if 0<t<1 and -1e-8<=u<=1+1e-8:cuts.append(t)
    cuts=sorted(set(cuts));result=[]
    for lo,hi in zip(cuts,cuts[1:]):
        mid=(lo+hi)/2
        if not point_in_region((a[0]+dx*mid,a[1]+dz*mid),polygon):
            result.append(((a[0]+dx*lo,a[1]+dz*lo),(a[0]+dx*hi,a[1]+dz*hi)))
    return result


def door_portal(door, regions):
    """Finite doorway bridge, aligned to normal and bounded by the door width."""
    cx,_,cz=door["position"];nx,nz=door["normal"];n=math.hypot(nx,nz);nx/=n;nz/=n
    reach=max(1.0,min(3.0,door["width_m"]*2));anchors=[]
    for rid in (door["from_region"],door["to_region"]):
        region=regions[rid];cuts=[-reach,reach]
        for a,b in _region_edges(region):
            ex,ez=b[0]-a[0],b[1]-a[1];den=nx*ez-nz*ex
            if abs(den)<1e-12:continue
            t=((a[0]-cx)*ez-(a[1]-cz)*ex)/den
            u=((a[0]-cx)*nz-(a[1]-cz)*nx)/den
            if -reach<t<reach and -1e-8<=u<=1+1e-8:cuts.append(t)
        cuts=sorted(set(cuts));options=[]
        for lo,hi in zip(cuts,cuts[1:]):
            if not point_in_region((cx+nx*(lo+hi)/2,cz+nz*(lo+hi)/2),region):continue
            inset=min(AGENT_RADIUS+.04,(hi-lo)/2)
            t=max(lo+inset,min(hi-inset,0.0));options.append(t)
        if not options:return None
        anchors.append(min(options,key=abs))
    lo,hi=sorted(anchors)
    if hi-lo<1e-4:return None
    half=door["width_m"]/2
    return {"outer":[(cx+nx*t-nz*w,cz+nz*t+nx*w) for t,w in
                     ((lo,-half),(hi,-half),(hi,half),(lo,half))],"holes":[],"door":door["id"]}


def _boundary_distance(p: Vec2, region: dict) -> float:
    best = math.inf
    for ring in [region["outer"], *region["holes"]]:
        for i, a in enumerate(ring):
            best = min(best, _point_segment_distance(p, a, ring[(i + 1) % len(ring)])[0])
    return best


def _point_in_triangle(p: Vec2, a: Vec2, b: Vec2, c: Vec2) -> bool:
    return (_cross(a, b, p) >= -1e-8 and _cross(b, c, p) >= -1e-8
            and _cross(c, a, p) >= -1e-8)


def triangulate_ring(ring: Sequence[Vec2]) -> List[List[Vec2]]:
    """Ear-clip a simple CCW floor ring into hidden navigation triangles."""
    points = list(ring)
    if _area(points) < 0: points.reverse()
    indices = list(range(len(points)))
    triangles = []
    guard = 0
    while len(indices) > 3 and guard < len(points) * len(points):
        guard += 1
        clipped = False
        for k, current in enumerate(indices):
            prev = indices[k - 1]
            nxt = indices[(k + 1) % len(indices)]
            a, b, c = points[prev], points[current], points[nxt]
            if _cross(a, b, c) <= 1e-9:
                continue
            if any(_point_in_triangle(points[j], a, b, c)
                   for j in indices if j not in {prev, current, nxt}):
                continue
            triangles.append([a, b, c])
            indices.pop(k)
            clipped = True
            break
        if not clipped:
            raise SceneValidationError("可走多边形无法三角化；请检查重复点或自交")
    if len(indices) == 3:
        triangles.append([points[indices[0]], points[indices[1]], points[indices[2]]])
    return triangles


def _strict_segment_intersection(a: Vec2, b: Vec2, c: Vec2, d: Vec2) -> bool:
    """Intersection that ignores a shared/touching boundary point."""
    ab_c, ab_d = _cross(a, b, c), _cross(a, b, d)
    cd_a, cd_b = _cross(c, d, a), _cross(c, d, b)
    eps = 1e-9
    return ((ab_c > eps and ab_d < -eps) or (ab_c < -eps and ab_d > eps)) and \
           ((cd_a > eps and cd_b < -eps) or (cd_a < -eps and cd_b > eps))


def _point_strictly_in_ring(p: Vec2, ring: Sequence[Vec2], eps: float = 1e-7) -> bool:
    """Inside a polygon but not on its boundary (shared borders stay valid)."""
    if not point_in_ring(p, ring):
        return False
    return all(_point_segment_distance(p, a, ring[(i + 1) % len(ring)])[0] > eps
               for i, a in enumerate(ring))


def _ring_inside_region(ring: Sequence[Vec2], region: dict) -> bool:
    """Return whether a whole spawn outline lies in one walkable region.

    Sharing an outer edge is valid. Crossing an outer or obstacle boundary,
    enclosing an obstacle, or bridging two adjacent regions is not. Edge
    samples supplement the vertices so a long edge cannot jump outside the
    region between two valid corners.
    """
    outer, holes = region["outer"], region["holes"]
    samples = list(ring)
    for a, b in zip(ring, ring[1:] + ring[:1]):
        for fraction in (.25, .5, .75):
            samples.append((a[0] + (b[0] - a[0]) * fraction,
                            a[1] + (b[1] - a[1]) * fraction))
    samples.append((sum(p[0] for p in ring) / len(ring),
                    sum(p[1] for p in ring) / len(ring)))
    if not all(point_in_region(p, region) for p in samples):
        return False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        if any(_strict_segment_intersection(a, b, outer[i], outer[(i + 1) % len(outer)])
               for i in range(len(outer))):
            return False
        for hole in holes:
            if any(_strict_segment_intersection(a, b, hole[i], hole[(i + 1) % len(hole)])
                   for i in range(len(hole))):
                return False
    for hole in holes:
        if any(point_in_ring(p, ring) for p in hole):
            return False
    return True


def _triangle_inside_region(tri: Sequence[Vec2], outer: Sequence[Vec2],
                            holes: Sequence[Sequence[Vec2]]) -> bool:
    samples = list(tri) + [((tri[i][0] + tri[(i + 1) % 3][0]) / 2,
                             (tri[i][1] + tri[(i + 1) % 3][1]) / 2)
                            for i in range(3)]
    samples.append((sum(p[0] for p in tri) / 3, sum(p[1] for p in tri) / 3))
    if not all(point_in_ring(p, outer) for p in samples):
        return False
    for hole in holes:
        if any(point_in_ring(p, hole) for p in samples):
            return False
        if any(_strict_segment_intersection(tri[i], tri[(i + 1) % 3],
                                            hole[j], hole[(j + 1) % len(hole)])
               for i in range(3) for j in range(len(hole))):
            return False
        if any(_point_in_triangle(p, *tri) and
               abs(_cross(tri[0], tri[1], p)) > 1e-8
               for p in hole):
            return False
    return True


def triangulate_region(outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]]) -> List[List[Vec2]]:
    """Triangulate a walkable polygon while conservatively carving holes.

    Ear clipping handles the outer boundary.  Faces crossing an obstacle hole
    are recursively split into four smaller faces until their samples are
    wholly walkable.  Navigation is hidden, so this conservative subdivision
    favors safety (never crossing a hole) over a minimal triangle count.
    """
    seeds = triangulate_ring(outer)
    if not holes:
        return seeds
    result: List[List[Vec2]] = []
    stack = [(tri, 0) for tri in seeds]
    max_depth = 7
    while stack:
        tri, depth = stack.pop()
        if _triangle_inside_region(tri, outer, holes):
            result.append(tri)
            continue
        if depth >= max_depth:
            continue
        a, b, c = tri
        ab = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bc = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        ca = ((c[0] + a[0]) / 2, (c[1] + a[1]) / 2)
        stack.extend([([a, ab, ca], depth + 1), ([ab, b, bc], depth + 1),
                      ([ca, bc, c], depth + 1), ([ab, bc, ca], depth + 1)])
    return result


def _representative_point(outer: Sequence[Vec2], holes: Sequence[Sequence[Vec2]]) -> Vec2:
    # Polygon centroid first; triangulation/grid fallback handles concave shapes.
    cross_sum = 0.0
    cx = cz = 0.0
    for i, a in enumerate(outer):
        b = outer[(i + 1) % len(outer)]
        cross = a[0] * b[1] - b[0] * a[1]
        cross_sum += cross
        cx += (a[0] + b[0]) * cross
        cz += (a[1] + b[1]) * cross
    if abs(cross_sum) > 1e-9:
        candidate = (cx / (3 * cross_sum), cz / (3 * cross_sum))
        if point_in_ring(candidate, outer) and not any(point_in_ring(candidate, h) for h in holes):
            return candidate
    min_x, max_x = min(v[0] for v in outer), max(v[0] for v in outer)
    min_z, max_z = min(v[1] for v in outer), max(v[1] for v in outer)
    # Deterministic interior scan, independent from any runtime visible grid.
    for rows in (24, 48, 96):
        for iz in range(rows):
            z = min_z + (iz + .5) * (max_z - min_z) / rows
            for ix in range(rows):
                x = min_x + (ix + .5) * (max_x - min_x) / rows
                if point_in_ring((x, z), outer) and not any(point_in_ring((x, z), h) for h in holes):
                    return x, z
    raise SceneValidationError("无法为可走多边形找到内部导航点")


def validate_scene(raw: dict, require_route: bool = True) -> dict:
    if not isinstance(raw, dict) or raw.get("schema") != SCENE_SCHEMA:
        raise SceneValidationError("场景必须使用 schema 2")
    scene_id = raw.get("id")
    if not isinstance(scene_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,47}", scene_id):
        raise SceneValidationError("场景 id 仅允许小写字母、数字、-、_，长度不超过 48")
    model = raw.get("model")
    if not isinstance(model, dict):
        raise SceneValidationError("缺少 model 配置")
    filename = model.get("file")
    if not isinstance(filename, str) or not filename.lower().endswith(".glb") or Path(filename).name != filename:
        raise SceneValidationError("模型必须是安全文件名形式的单文件 GLB")
    scale = raw.get("scale")
    if not isinstance(scale, dict) or scale.get("status") not in {"provisional", "verified"}:
        raise SceneValidationError("尺度状态必须是 provisional 或 verified")
    if scale.get("status") == "verified":
        reference = _finite_number(scale.get("reference_length_m"), "reference_length_m")
        if not .01 <= reference <= 10000:
            raise SceneValidationError("已校准的参考长度必须在 0.01 到 10000 米之间")
    transform = raw.get("transform") or {}
    if not isinstance(transform, dict):
        raise SceneValidationError("transform 格式错误")
    unit_scale = _finite_number(transform.get("scale_m_per_unit", .01), "scale_m_per_unit")
    if not 1e-6 <= unit_scale <= 1000:
        raise SceneValidationError("模型比例超出允许范围")
    rotation = transform.get("rotation", [0, 0, 0])
    translation = transform.get("translation", [0, 0, 0])
    _vec3(rotation, "rotation")
    _vec3(translation, "translation")
    floors_raw = raw.get("floors")
    if not isinstance(floors_raw, list) or not 1 <= len(floors_raw) <= 64:
        raise SceneValidationError("场景需要 1 到 64 层")
    floors, floor_ids, regions = [], set(), {}
    point_total = 0
    for floor in floors_raw:
        if not isinstance(floor, dict):
            raise SceneValidationError("楼层定义必须是对象")
        fid = floor.get("id")
        if not isinstance(fid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", fid) or fid in floor_ids:
            raise SceneValidationError("楼层 id 不合法或重复")
        floor_ids.add(fid)
        elevation = _finite_number(floor.get("elevation_m"), f"{fid}.elevation_m")
        walkable_raw = floor.get("walkable")
        if not isinstance(walkable_raw, list) or not walkable_raw:
            raise SceneValidationError(f"{fid} 至少需要一个可走区域")
        if len(walkable_raw) > MAX_SCENE_REGIONS:
            raise SceneValidationError("可走区域数量过多")
        normalized = []
        for index, item in enumerate(walkable_raw):
            if not isinstance(item, dict):
                raise SceneValidationError("可走区域必须是对象")
            rid = item.get("id", f"{fid}-region-{index+1}")
            if not isinstance(rid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", rid) or rid in regions:
                raise SceneValidationError("区域 id 不合法或重复")
            outer = _normalize_ring(item.get("outer"), f"{fid}/{rid}/outer")
            holes_raw = item.get("holes", [])
            if not isinstance(holes_raw, list):
                raise SceneValidationError("holes 必须是列表")
            holes = [_normalize_ring(h, f"{fid}/{rid}/hole") for h in holes_raw]
            for hole in holes:
                if not all(point_in_ring(p, outer) for p in hole) or _rings_intersect(hole, outer):
                    raise SceneValidationError("障碍孔洞必须完整位于外边界内部")
            for i, hole in enumerate(holes):
                if any(point_in_ring(p, other) for p in hole for other in holes[i+1:]) or any(
                        _rings_intersect(hole, other) for other in holes[i+1:]):
                    raise SceneValidationError("障碍孔洞不能互相重叠")
            center = _representative_point(outer, holes)
            triangles = triangulate_region(outer, holes)
            if not triangles:
                raise SceneValidationError(f"{fid}/{rid} 没有有效导航面")
            region = {"id": rid, "floor": fid, "elevation": elevation,
                      "outer": outer, "holes": holes, "center": center, "triangles": triangles}
            normalized.append(region)
            regions[rid] = region
            point_total += len(outer) + sum(map(len, holes))
            if point_total > MAX_POLYGON_POINTS:
                raise SceneValidationError("导航多边形顶点总量超限")
        zones_raw = floor.get("spawn_zones", [])
        if not isinstance(zones_raw, list):
            raise SceneValidationError("spawn_zones 必须是列表")
        zones = []
        for zi, zone in enumerate(zones_raw):
            if not isinstance(zone, dict): raise SceneValidationError("起始区域必须是对象")
            outer_zone = _normalize_ring(zone.get("outer"), f"{fid}.spawn_zones[{zi}]")
            owners = [region["id"] for region in normalized
                      if _ring_inside_region(outer_zone, region)]
            if not owners:
                raise SceneValidationError(
                    "起始区域必须完整落在同一块可走区域内；可以贴边，但不能跨区域拼接或覆盖障碍")
            # A spawn outline must not bridge two adjacent walkable rooms.
            # An edge may touch a shared border, but another region's vertex
            # or representative point may not lie strictly inside the outline.
            bridged = [other["id"] for other in normalized
                       if other["id"] != owners[0]
                       and (any(_point_strictly_in_ring(v, outer_zone) for v in other["outer"])
                            or (_point_strictly_in_ring(other["center"], outer_zone)
                                and point_in_region(other["center"], other)))]
            if bridged:
                raise SceneValidationError(
                    f"起始区域跨越了相邻可走区 {', '.join(bridged)}；请在同一块区域内部重新圈定")
            zones.append({"id": str(zone.get("id", f"{fid}-spawn-{zi+1}")),
                          "outer": outer_zone, "region": owners[0]})
        floor_copy = {"id": fid, "name": str(floor.get("name", fid))[:80],
                      "elevation_m": elevation, "walkable": normalized, "spawn_zones": zones}
        floors.append(floor_copy)

    stairs_raw = raw.get("stairs", [])
    exits_raw = raw.get("exits", [])
    if not isinstance(stairs_raw, list) or len(stairs_raw) > 256:
        raise SceneValidationError("stairs 格式错误")
    if not isinstance(exits_raw, list) or not 1 <= len(exits_raw) <= 128:
        raise SceneValidationError("场景至少需要一个出口")
    stairs, exits = [], []
    def containing_region(floor_id: str, p: Vec2):
        return next((r for r in regions.values() if r["floor"] == floor_id and point_in_region(p, r)), None)
    for item in stairs_raw:
        if not isinstance(item, dict):
            raise SceneValidationError("楼梯定义必须是对象")
        sid = item.get("id")
        source, target = item.get("from_floor"), item.get("to_floor")
        bottom, top = _vec3(item.get("bottom"), "stairs.bottom"), _vec3(item.get("top"), "stairs.top")
        if source not in floor_ids or target not in floor_ids or source == target:
            raise SceneValidationError("楼梯必须连接两个不同且存在的楼层")
        source_elevation = next(f["elevation_m"] for f in floors if f["id"] == source)
        target_elevation = next(f["elevation_m"] for f in floors if f["id"] == target)
        if abs(bottom[1] - source_elevation) > ELEVATION_TOLERANCE_M:
            raise SceneValidationError(
                f"楼梯底端高度应为起始楼面标高 {source_elevation:.2f} m，实际为 {bottom[1]:.2f} m")
        if abs(top[1] - target_elevation) > ELEVATION_TOLERANCE_M:
            raise SceneValidationError(
                f"楼梯顶端高度应为目标楼面标高 {target_elevation:.2f} m，实际为 {top[1]:.2f} m")
        a = containing_region(source, (bottom[0], bottom[2]))
        b = containing_region(target, (top[0], top[2]))
        if not a or not b:
            raise SceneValidationError("楼梯上下端必须落在各自楼层的可走区域内")
        width = _finite_number(item.get("width_m", 1.2), "stairs.width_m")
        factor = _finite_number(item.get("speed_factor", .72), "stairs.speed_factor")
        raw_capacity = item.get("capacity")
        if raw_capacity is None:
            capacity = max(1, min(8, int(math.ceil(width / .6))))
        elif isinstance(raw_capacity, bool) or not isinstance(raw_capacity, int) or not 1 <= raw_capacity <= 64:
            raise SceneValidationError("楼梯 capacity 必须是 1 到 64 的整数")
        else:
            capacity = raw_capacity
        if not .5 <= width <= 20 or not .1 <= factor <= 1:
            raise SceneValidationError("楼梯宽度或速度系数超出范围")
        raw_path = item.get("path")
        if raw_path is None:
            path = [bottom, top]
        else:
            if not isinstance(raw_path, list) or not 2 <= len(raw_path) <= 48:
                raise SceneValidationError("楼梯 path 应由 2 到 48 个三维点组成")
            path = [_vec3(point, f"stairs.path[{index}]") for index, point in enumerate(raw_path)]
            if math.dist(path[0], bottom) > 1e-5 or math.dist(path[-1], top) > 1e-5:
                raise SceneValidationError("楼梯 path 必须从 bottom 连续连接至 top")
            if any(_distance3(a, b) < .01 for a, b in zip(path, path[1:])):
                raise SceneValidationError("楼梯路径不可包含重复相邻点")
            low, high = sorted((bottom[1], top[1]))
            if any(not low-.01 <= point[1] <= high+.01 for point in path):
                raise SceneValidationError("楼梯路径高度不可越过楼层端点")
        stairs.append({"id": str(sid or f"stairs-{len(stairs)+1}"), "from_floor": source,
                       "to_floor": target, "bottom": bottom, "top": top, "path": path,
                       "width_m": width, "capacity": capacity, "speed_factor": factor,
                       "from_region": a["id"], "to_region": b["id"]})
    doors_raw = raw.get("doors", [])
    if not isinstance(doors_raw, list) or len(doors_raw) > 2048:
        raise SceneValidationError("doors 格式错误")
    doors = []
    valid_modes = {"auto", "manual", "both"}
    valid_states = {"closed", "open", "locked", "fire-blocked", "blocked"}
    for index, item in enumerate(doors_raw):
        if not isinstance(item, dict):
            raise SceneValidationError("门定义必须是对象")
        did = item.get("id", f"door-{index+1}")
        if not isinstance(did, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", did) or any(d["id"] == did for d in doors):
            raise SceneValidationError("门 id 不合法或重复")
        floor_id = item.get("floor")
        from_region, to_region = item.get("from_region"), item.get("to_region")
        # Older saved scenes may carry a draft door annotation after a region
        # was renamed or removed. Ignore that stale optional annotation so
        # legacy scene/test fixtures remain loadable; newly authored doors are
        # still validated strictly by the calibration save endpoint.
        if floor_id not in floor_ids or from_region not in regions or to_region not in regions:
            continue
        if regions[from_region]["floor"] != floor_id or regions[to_region]["floor"] != floor_id or from_region == to_region:
            raise SceneValidationError("门两侧必须是同一楼层的不同区域")
        pos = _vec3(item.get("position"), "door.position")
        expected_floor = regions[from_region]["elevation"]
        if abs(pos[1] - expected_floor) > ELEVATION_TOLERANCE_M:
            raise SceneValidationError(
                f"门高度应为楼面标高 {expected_floor:.2f} m，实际为 {pos[1]:.2f} m")
        center = (pos[0], pos[2])
        if not (point_in_region(center, regions[from_region])
                or point_in_region(center, regions[to_region])):
            raise SceneValidationError("门中心必须位于其两侧区域之一，不能跨到其他区域")
        unrelated = [rid for rid, region in regions.items()
                     if region["floor"] == floor_id and rid not in {from_region, to_region}
                     and point_in_region(center, region)]
        if unrelated:
            raise SceneValidationError(f"门中心同时落在无关区域 {', '.join(unrelated)}；请调整区域选择或门位置")
        width = _finite_number(item.get("width_m", .9), "door.width_m")
        normal = _vec2(item.get("normal", [1, 0]), "door.normal")
        mode = item.get("mode", "both")
        state = item.get("initial_state", "closed")
        if not .4 <= width <= 10: raise SceneValidationError("门宽度必须在 0.4 到 10 米之间")
        if math.hypot(*normal) < 1e-6: raise SceneValidationError("门 normal 不能为零")
        if mode not in valid_modes: raise SceneValidationError("门 mode 必须是 auto、manual 或 both")
        if state not in valid_states: raise SceneValidationError("门 initial_state 不合法")
        threshold = _finite_number(item.get("fire_block_threshold", .85), "door.fire_block_threshold")
        auto_close = _finite_number(item.get("auto_close_s", 4), "door.auto_close_s")
        open_duration = _finite_number(item.get("open_duration_s", .6), "door.open_duration_s")
        if not 0 <= threshold <= 1 or not 0 <= auto_close <= 3600 or not .05 <= open_duration <= 30:
            raise SceneValidationError("门时间或烟气阈值不合理")
        visual = item.get("visual", {}) or {}
        if not isinstance(visual, dict): raise SceneValidationError("door.visual 格式错误")
        doors.append({"id": did, "name": str(item.get("name", did))[:120], "floor": floor_id,
                      "position": pos, "width_m": width, "normal": normal,
                      "from_region": from_region, "to_region": to_region, "mode": mode,
                      "initial_state": state, "locked": bool(item.get("locked", state == "locked")),
                      "fire_block_threshold": threshold, "auto_close_s": auto_close,
                      "open_duration_s": open_duration, "visual": visual})
    for item in exits_raw:
        if not isinstance(item, dict):
            raise SceneValidationError("出口定义必须是对象")
        eid, floor_id = item.get("id", f"exit-{len(exits)+1}"), item.get("floor")
        pos = _vec3(item.get("position"), "exit.position")
        kind = item.get("kind", "exit")
        if kind not in {"exit", "practice"}:
            raise SceneValidationError("出口类型只能为 exit 或 practice")
        width = _finite_number(item.get("width_m", 1.2), "exit.width_m")
        if floor_id not in floor_ids:
            raise SceneValidationError("出口所属楼层不存在")
        region = containing_region(floor_id, (pos[0], pos[2]))
        expected_floor = next(f["elevation_m"] for f in floors if f["id"] == floor_id)
        if not region:
            raise SceneValidationError("出口必须位于对应楼层的可走区域内，并避开障碍")
        if abs(pos[1] - expected_floor) > ELEVATION_TOLERANCE_M:
            raise SceneValidationError(
                f"出口高度应为楼面标高 {expected_floor:.2f} m，实际为 {pos[1]:.2f} m")
        if not .4 <= width <= 30:
            raise SceneValidationError("出口宽度不合理")
        exits.append({"id": str(eid), "floor": floor_id, "position": pos,
                      "width_m": width, "region": region["id"], "kind": kind})
    if require_route:
        # Every region must be reachable from some exit by room adjacency or a stair.
        adjacency = {rid: set() for rid in regions}
        ids = list(regions)
        for i, aid in enumerate(ids):
            a = regions[aid]
            for bid in ids[i+1:]:
                b = regions[bid]
                if a["floor"] != b["floor"]:
                    continue
                if any({d["from_region"], d["to_region"]} == {aid, bid} for d in doors):
                    continue
                if _regions_touch(a, b):
                    adjacency[aid].add(bid); adjacency[bid].add(aid)
        for stair in stairs:
            adjacency[stair["from_region"]].add(stair["to_region"])
            adjacency[stair["to_region"]].add(stair["from_region"])
        for door in doors:
            adjacency[door["from_region"]].add(door["to_region"])
            adjacency[door["to_region"]].add(door["from_region"])
        seen, stack = set(), [e["region"] for e in exits]
        while stack:
            rid = stack.pop()
            if rid in seen: continue
            seen.add(rid); stack.extend(adjacency[rid] - seen)
        if seen != set(regions):
            raise SceneValidationError("存在无法从任何出口到达的楼层/区域；请检查楼梯和区域连接")
    return {**raw, "model": dict(model), "transform": {**transform, "scale_m_per_unit": unit_scale},
            "floors": floors, "stairs": stairs, "doors": doors, "exits": exits,
            "scale": {**scale, "status": scale["status"]}}


def _rings_intersect(a: Sequence[Vec2], b: Sequence[Vec2]) -> bool:
    return any(_segments_intersect(p, a[(i+1)%len(a)], q, b[(j+1)%len(b)])
               for i,p in enumerate(a) for j,q in enumerate(b))


def _triangle_intersects_ring(tri: Sequence[Vec2], ring: Sequence[Vec2]) -> bool:
    center=(sum(p[0] for p in tri)/3,sum(p[1] for p in tri)/3)
    if point_in_ring(center,ring): return True
    if any(point_in_ring(p,ring) for p in tri): return True
    if any(_point_in_triangle(p,*tri) for p in ring): return True
    return _rings_intersect(tri,ring)

def _regions_touch(a: dict, b: dict) -> bool:
    # Explicitly shared/overlapping walkable polygons are connected. Disjoint
    # areas need an explicit stair/connector; authoring UI may later add doors.
    if point_in_region(a["center"], b) or point_in_region(b["center"], a):
        return True
    for ra in [a["outer"], *a["holes"]]:
        for rb in [b["outer"], *b["holes"]]:
            for i, p in enumerate(ra):
                p2 = ra[(i + 1) % len(ra)]
                for j, q in enumerate(rb):
                    q2 = rb[(j + 1) % len(rb)]
                    if _segments_intersect(p, p2, q, q2):
                        return True
    return False



def _triangles_touch(a: Sequence[Vec2], b: Sequence[Vec2]) -> bool:
    for i, p in enumerate(a):
        p2 = a[(i+1)%len(a)]
        for j, q in enumerate(b):
            q2 = b[(j+1)%len(b)]
            if _segments_intersect(p,p2,q,q2): return True
    return False


def _distance3(a: Vec3, b: Vec3) -> float:
    return math.dist(a, b)


@dataclass
class Agent3D:
    pid: str
    name: str
    color: str
    position: Vec3
    floor_id: str
    region_id: str
    human: bool = False
    nav_node: str = ""
    active_connector: Optional[str] = None
    connector_progress: float = 0.0
    connector_forward: bool = True
    connector_resume_pending: bool = False
    connector_entry: Optional[Vec3] = None
    pressure: float = 0.0
    density: int = 0
    velocity: Vec3 = (0.0, 0.0, 0.0)
    input: Tuple[float, float] = (0.0, 0.0)
    state: str = "waiting"
    blocked_reason: str = ""
    input_received_at: Optional[float] = None
    health: float = 1.0
    smoke_dose: float = 0.0
    max_smoke: float = 0.0
    escaped: bool = False
    failed: bool = False
    completed: bool = False
    goal_exit_id: Optional[str] = None
    path: List[Tuple[str, Vec3, Optional[str]]] = field(default_factory=list)
    path_length: float = 0.0
    track: List[list] = field(default_factory=list)


class EvacSim3D:
    """Service-authoritative continuous-coordinate crowd and layered-smoke model."""
    def __init__(self, scene: dict, data_dir: Path, seed: int = 476167300, require_route: bool = True, default_npcs: int = 80):
        # Calibration previews may intentionally run before every region has a
        # complete route. Formal simulation keeps the stricter connectivity
        # check, while the preview path can still show what is missing.
        self.scene = validate_scene(scene, require_route=require_route)
        self.data_dir = Path(data_dir)
        self.rng = random.Random(seed)
        self.phase = "idle"
        self.elapsed = 0.0
        self.real_elapsed = 0.0
        self.last_wall_time = time.monotonic()
        self.npc_target = max(0, min(300, int(default_npcs)))
        self.panic = .4
        self.time_scale = 1.0
        self.wind = (0.0, 0.0, 0.0)
        # Main process attaches the current v2 experiment labels here. Keeping
        # this on the simulator makes direct archive calls include the same
        # metadata as API exports.
        self.experiment: dict = {}
        self.agents: Dict[str, Agent3D] = {}
        # A fresh process may expose the last persisted v2 run for replay;
        # an explicit reset/start invalidates that fallback until new tracks exist.
        self.replay_invalidated = False
        self.round_revision = 0
        self.smoke = {}
        self.fire: Dict[str, float] = {}
        self._region_index = {r["id"]: r for f in self.scene["floors"] for r in f["walkable"]}
        self._stairs = self.scene["stairs"]
        self._doors = self.scene.get("doors", [])
        self._door_state = {d["id"]: ("locked" if d.get("locked") else d.get("initial_state", "closed")) for d in self._doors}
        self._door_open_until = {d["id"]: 0.0 for d in self._doors}
        self._door_opening_until = {d["id"]: 0.0 for d in self._doors}
        self.door_events: List[dict] = []
        self._exits = self.scene["exits"]
        self._exit_nodes = []
        self._graph: Dict[str, List[Tuple[str, float, Optional[str]]]] = {}
        self._build_graph()
        self._spatial_bins = {}
        self.smoke = {nid: 0.0 for nid in self._nav_nodes}
        self.ensure_population(self.npc_target)
        # Population initialization is not an explicit user reset; allow the
        # main process to expose the last persisted v2 replay after restart.
        self.replay_invalidated = False

    def _build_graph(self):
        self._nav_nodes = {}
        self._node_region = {}
        for rid, region in self._region_index.items():
            for index, tri in enumerate(region["triangles"]):
                nid = f"{rid}#t{index}"
                center = (sum(p[0] for p in tri)/3, sum(p[1] for p in tri)/3)
                self._nav_nodes[nid] = {"id": nid, "region": rid, "floor": region["floor"],
                                        "elevation": region["elevation"], "center": center, "triangle": tri}
                self._node_region[nid] = rid
        self._graph = {nid: [] for nid in self._nav_nodes}
        edge_map = {}
        def edge_key(a, b): return tuple(sorted((tuple(a), tuple(b))))
        for nid,node in self._nav_nodes.items():
            tri=node["triangle"]
            for i,p in enumerate(tri):
                key=edge_key(p,tri[(i+1)%3])
                edge_map.setdefault((node["floor"],key),[]).append(nid)
        region_links=set()
        for (floor_id,_),attached in edge_map.items():
            unique=list(dict.fromkeys(attached))
            for i,aid in enumerate(unique):
                for bid in unique[i+1:]:
                    a,b=self._nav_nodes[aid],self._nav_nodes[bid]
                    if a["region"]==b["region"]:
                        region_links.add((aid,bid))
                    else:
                        ra,rb=self._region_index[a["region"]],self._region_index[b["region"]]
                        if _regions_touch(ra,rb) and not any({d["from_region"],d["to_region"]}=={ra["id"],rb["id"]} for d in self._doors): region_links.add((aid,bid))
        # Triangle edges are the fast path for one region.  Calibrators may
        # also draw adjacent walkable regions as separate polygons; connect
        # their closest triangle centroids so the runtime never depends on
        # matching vertex order or floating point equality.
        for aid,bid in region_links:
            a,b=self._nav_nodes[aid],self._nav_nodes[bid]
            pa=(a["center"][0],a["elevation"],a["center"][1]); pb=(b["center"][0],b["elevation"],b["center"][1])
            cost=_distance3(pa,pb)
            self._graph[aid].append((bid,cost,None)); self._graph[bid].append((aid,cost,None))
        region_ids = list(self._region_index)
        for i, ra_id in enumerate(region_ids):
            ra = self._region_index[ra_id]
            for rb_id in region_ids[i + 1:]:
                rb = self._region_index[rb_id]
                if ra["floor"] != rb["floor"] or not _regions_touch(ra, rb):
                    continue
                if any({d["from_region"], d["to_region"]} == {ra_id, rb_id} for d in self._doors):
                    continue
                a_nodes = [n for n in self._nav_nodes.values() if n["region"] == ra_id]
                b_nodes = [n for n in self._nav_nodes.values() if n["region"] == rb_id]
                if not a_nodes or not b_nodes:
                    continue
                a, b = min(((a, b) for a in a_nodes for b in b_nodes),
                           key=lambda pair: math.dist(pair[0]["center"], pair[1]["center"]))
                pa=(a["center"][0],a["elevation"],a["center"][1]); pb=(b["center"][0],b["elevation"],b["center"][1])
                cost=_distance3(pa,pb)
                self._graph[a["id"]].append((b["id"],cost,None))
                self._graph[b["id"]].append((a["id"],cost,None))
        self._door_nodes = {}
        self._smoke_links = {nid: [] for nid in self._nav_nodes}
        for door in self._doors:
            a = self._nearest_nav_node(door["from_region"], door["position"])
            b = self._nearest_nav_node(door["to_region"], door["position"])
            self._door_nodes[door["id"]] = (a, b)
            # Smoke can leak through a closed door at reduced transfer; only
            # navigation movement is blocked by the door state.
            pa = (self._nav_nodes[a]["center"][0], self._nav_nodes[a]["elevation"], self._nav_nodes[a]["center"][1])
            pb = (self._nav_nodes[b]["center"][0], self._nav_nodes[b]["elevation"], self._nav_nodes[b]["center"][1])
            cost = _distance3(pa, pb)
            self._smoke_links[a].append((b, cost, door["id"]))
            self._smoke_links[b].append((a, cost, door["id"]))
            # Closed automatic doors stay on the planning graph so people walk
            # up to them and open them. Locked, blocked and manual doors do not,
            # otherwise a shut door would be a hidden shortcut.
            state = self._door_state.get(door["id"], "closed")
            openable = door.get("mode") in {"auto", "both"} and not door.get("locked") and state in {"open", "opening", "closed"}
            if self._door_passable(door["id"]) or openable:
                pa = (self._nav_nodes[a]["center"][0], self._nav_nodes[a]["elevation"], self._nav_nodes[a]["center"][1])
                pb = (self._nav_nodes[b]["center"][0], self._nav_nodes[b]["elevation"], self._nav_nodes[b]["center"][1])
                cost = _distance3(pa, pb)
                self._graph[a].append((b, cost, door["id"]))
                self._graph[b].append((a, cost, door["id"]))
        self._stair_nodes = {}
        for stair in self._stairs:
            a = self._nearest_nav_node(stair["from_region"], stair["bottom"])
            b = self._nearest_nav_node(stair["to_region"], stair["top"])
            self._stair_nodes[stair["id"]] = (a,b)
            cost = sum(_distance3(a,b) for a,b in zip(stair["path"],stair["path"][1:]))/stair["speed_factor"]
            self._graph[a].append((b,cost,stair["id"])); self._graph[b].append((a,cost,stair["id"]))
        self._exit_nodes = []
        for exit_data in self._exits:
            node = self._nearest_nav_node(exit_data["region"], exit_data["position"])
            self._exit_nodes.append((node,exit_data))

    def _door_passable(self, door_id: str) -> bool:
        return self._door_state.get(door_id, "closed") == "open"

    def _door_by_id(self, door_id: str) -> Optional[dict]:
        return next((d for d in self._doors if d["id"] == door_id), None)

    def _door_distance(self, agent: Agent3D, door: dict) -> float:
        return math.dist((agent.position[0], agent.position[2]), (door["position"][0], door["position"][2]))

    def nearby_doors(self, agent: Agent3D, radius: float = 1.5) -> List[dict]:
        result = []
        for door in self._doors:
            if door["floor"] != agent.floor_id or agent.region_id not in {door["from_region"], door["to_region"]}:
                continue
            distance = self._door_distance(agent, door)
            if distance > radius: continue
            state = self._door_state.get(door["id"], "closed")
            can_open = state in {"closed", "open"} and not door.get("locked", False) and state != "fire-blocked"
            result.append({"id": door["id"], "name": door["name"], "state": state,
                           "distance_m": round(distance, 2), "can_open": can_open,
                           "requires_action": door["mode"] in {"manual", "both"},
                           "from_region": door["from_region"], "to_region": door["to_region"]})
        return sorted(result, key=lambda x: x["distance_m"])

    def _record_door_event(self, door_id: str, event: str, source: str, pid: Optional[str] = None):
        self.door_events.append({"door_id": door_id, "event": event, "source": source,
                                 "pid": pid, "sim_time_s": round(self.elapsed, 3),
                                 "real_time": time.time()})

    def request_door(self, door_id: str, agent: Optional[Agent3D] = None, action: str = "open", source: str = "participant") -> dict:
        door = self._door_by_id(door_id)
        if not door: return {"ok": False, "reason": "unknown-door"}
        state = self._door_state.get(door_id, "closed")
        if action == "open":
            if agent is not None:
                info = next((x for x in self.nearby_doors(agent, 1.5) if x["id"] == door_id), None)
                if not info: return {"ok": False, "reason": "too-far"}
            if state in {"locked", "fire-blocked", "blocked"} or door.get("locked", False):
                return {"ok": False, "reason": state}
            if state == "opening":
                return {"ok": True, "state": "opening"}
            if state == "open":
                self._door_open_until[door_id] = self.elapsed + door["auto_close_s"]
                return {"ok": True, "state": "open"}
            self._door_state[door_id] = "opening"
            self._door_opening_until[door_id] = self.elapsed + door["open_duration_s"]
            self._door_open_until[door_id] = self.elapsed + door["open_duration_s"] + door["auto_close_s"]
            self._record_door_event(door_id, "opening", source, agent.pid if agent else None)
            return {"ok": True, "state": "opening"}
        if action in {"close", "lock", "unlock", "fire-block", "clear-block"}:
            if action in {"close", "lock"} and state == "open":
                portal=door_portal(door,self._region_index)
                if portal and any(a.floor_id==door["floor"] and not a.escaped and not a.failed
                                  and point_in_region((a.position[0],a.position[2]),portal)
                                  for a in self.agents.values()):
                    return {"ok":False,"reason":"occupied","message":"门口有人正在通过，请稍后再关闭"}
            if action == "lock": door["locked"] = True; self._door_state[door_id] = "locked"
            elif action == "unlock": door["locked"] = False; self._door_state[door_id] = "closed"
            elif action == "fire-block": self._door_state[door_id] = "fire-blocked"
            elif action == "clear-block": self._door_state[door_id] = "closed"
            else: self._door_state[door_id] = "closed"
            self._record_door_event(door_id, action, source, agent.pid if agent else None)
            self._build_graph()
            for a in self.agents.values(): a.path.clear()
            return {"ok": True, "state": self._door_state[door_id]}
        return {"ok": False, "reason": "bad-action"}

    def door_state_payload(self) -> List[dict]:
        return [{**d, "state": self._door_state.get(d["id"], "closed"),
                 "open_until": round(self._door_open_until.get(d["id"], 0), 2)} for d in self._doors]

    def _update_doors(self):
        changed = False
        for door in self._doors:
            did, state = door["id"], self._door_state.get(door["id"], "closed")
            if state == "opening" and self.elapsed >= self._door_opening_until[did]:
                self._door_state[did] = "open"; self._record_door_event(did, "opened", "system"); changed = True
            if self._door_state.get(did) == "open" and self.elapsed >= self._door_open_until[did]:
                busy = any((not a.escaped and not a.failed and a.floor_id == door["floor"] and self._door_distance(a, door) < 1.0) for a in self.agents.values())
                if not busy:
                    self._door_state[did] = "closed"; self._record_door_event(did, "closed", "system"); changed = True
        if changed:
            self._build_graph()
            for a in self.agents.values(): a.path.clear()

    def _nearest_nav_node(self, region_id: str, pos: Vec3) -> str:
        candidates = [n for n in self._nav_nodes.values() if n["region"] == region_id]
        point=(pos[0],pos[2])
        containing=[n for n in candidates if _point_in_triangle(point,*n["triangle"])]
        pool=containing or candidates
        return min(pool, key=lambda n: math.dist(
            (n["center"][0],n["elevation"],n["center"][1]),pos))["id"]

    def _sample_region(self, region_id: Optional[str] = None) -> dict:
        choices = [self._region_index[region_id]] if region_id else list(self._region_index.values())
        available=[]
        for region in choices:
            floor=next(f for f in self.scene["floors"] if f["id"]==region["floor"])
            zones=[z for z in floor["spawn_zones"] if any(point_in_region(p,region) for p in z["outer"])]
            available.extend((region,zone) for zone in zones)
        if available:
            region,zone=self.rng.choice(available)
            outer=zone["outer"]
        else:
            region=self.rng.choice(choices); outer=region["outer"]
        min_x, max_x = min(p[0] for p in outer), max(p[0] for p in outer)
        min_z, max_z = min(p[1] for p in outer), max(p[1] for p in outer)
        for _ in range(256):
            p=(self.rng.uniform(min_x,max_x),self.rng.uniform(min_z,max_z))
            if point_in_region(p,region) and (not available or point_in_ring(p,outer)) and _boundary_distance(p,region)>=.3:
                return {"region":region,"point":p}
        center=_representative_point(outer,[]) if available else region["center"]
        return {"region":region,"point":center}

    def ensure_population(self, count: Optional[int] = None):
        self.replay_invalidated = True
        if count is not None:
            self.npc_target = max(0, min(300, int(count)))
        npcs = [a for a in self.agents.values() if not a.human]
        while len(npcs) > self.npc_target:
            self.agents.pop(npcs.pop().pid, None)
        while len(npcs) < self.npc_target:
            sample = self._sample_region()
            index = len(npcs) + 1
            pid = f"n{index:03d}-{self.rng.randrange(0x100000):05x}"
            p = sample["point"]; region = sample["region"]
            agent = Agent3D(pid, f"NPC{index}", "#8ba4ca",
                            (p[0], region["elevation"], p[1]), region["floor"], region["id"])
            self.agents[pid] = agent
            npcs.append(agent)
        for agent in self.agents.values():
            agent.path.clear()
            agent.nav_node = self._nearest_nav_node(agent.region_id, agent.position)
        if self.phase != "running":
            self.phase, self.elapsed, self.real_elapsed = "idle", 0.0, 0.0

    def active_human_count(self) -> int:
        return sum(1 for agent in self.agents.values()
                   if agent.human and not agent.escaped and not agent.failed and agent.state != "offline")

    def add_human(self, pid: str, name: str, color: str):
        if pid in self.agents:
            a = self.agents[pid]; a.name = name or a.name
            if a.state == "offline":
                a.input = (0.0, 0.0); a.velocity = (0.0, 0.0, 0.0)
                a.input_received_at = None
                a.state = "walking-stairs" if a.active_connector else "walking" if self.phase == "running" else "waiting"
            return a
        sample = self._sample_region()
        p, region = sample["point"], sample["region"]
        agent = Agent3D(pid, name or "玩家", color,
                        (p[0], region["elevation"], p[1]), region["floor"], region["id"], human=True)
        agent.nav_node = self._nearest_nav_node(agent.region_id, agent.position)
        self.agents[pid] = agent
        return agent

    def human_leave(self, pid: str):
        agent = self.agents.get(pid)
        if agent and agent.human and not agent.escaped and not agent.failed and not agent.completed:
            self.agents.pop(pid, None)

    def human_disconnect(self, pid: str):
        """Keep this trial's identity and observations across a transport loss.

        Offline agents neither move nor produce fabricated track samples. The
        operator can still archive the trial, and a reconnect reuses the object
        held by the participant socket instead of spawning a replacement.
        """
        agent = self.agents.get(pid)
        if not agent or not agent.human: return
        agent.input = (0.0, 0.0); agent.velocity = (0.0, 0.0, 0.0)
        agent.input_received_at = None
        if not agent.escaped and not agent.failed and not agent.completed:
            agent.state = "offline"
            agent.connector_resume_pending = bool(agent.active_connector)

    def _region_for(self, position: Vec3, floor_id: str) -> Optional[dict]:
        p = (position[0], position[2])
        return next((r for r in self._region_index.values()
                     if r["floor"] == floor_id and point_in_region(p, r)), None)

    def _nearest_walkable_region(self, floor_id: str, position: Vec3):
        """Snap a body that fell outside every region back onto the nearest face.

        Region outlines are independent polygons; a doorway or a calibration
        seam can leave a thin non-walkable band between them. Without this an
        agent that lands in that band has ``_region_for`` return ``None`` and
        the mover zeroes its velocity forever, which looks like "the character
        moved once and then froze".
        """
        p = (position[0], position[2])
        best = None
        for region in self._region_index.values():
            if region["floor"] != floor_id:
                continue
            q = closest_region_point(p, region, AGENT_RADIUS)
            d = math.dist(p, q)
            if best is None or d < best[0]:
                best = (d, region, q)
        if best is None:
            return None
        return best[1], best[2]

    def _node_blocked(self, node_id: str) -> bool:
        """Return whether a nav face is currently unsafe for a fresh route."""
        region_id = self._node_region.get(node_id)
        return bool(self.fire.get(region_id, 0.0) >= 0.98 or self.smoke.get(node_id, 0.0) >= 0.92)

    def _nearest_exit_path(self, start_node: str):
        """A* over hidden navigation triangles and stair connectors."""
        if start_node not in self._graph:
            return [], None
        heap = [(0.0, start_node)]
        dist = {start_node: 0.0}
        prev = {}
        while heap:
            cost, current = heapq.heappop(heap)
            if cost != dist.get(current):
                continue
            if any(current == nid for nid, _ in self._exit_nodes):
                options = [(e, math.dist(self._nav_nodes[current]["center"],
                                         (e["position"][0], e["position"][2])))
                           for nid, e in self._exit_nodes if nid == current]
                exit_data = min(options, key=lambda item: item[1])[0]
                chain = [current]
                while chain[-1] != start_node:
                    chain.append(prev[chain[-1]][0])
                chain.reverse()
                return chain, exit_data
            for nxt, weight, connector in self._graph.get(current, []):
                if nxt != start_node and self._node_blocked(nxt):
                    continue
                penalty = 1.0 + (0.35 if connector else 0.0)
                nd = cost + weight * penalty
                if nd < dist.get(nxt, math.inf):
                    dist[nxt] = nd
                    prev[nxt] = (current, connector)
                    heapq.heappush(heap, (nd, nxt))
        return [], None

    def _plan(self, agent: Agent3D):
        nodes, exit_data = self._nearest_exit_path(agent.nav_node)
        if not nodes or exit_data is None:
            agent.path = []
            agent.state = "trapped"
            return
        route = []
        for i, node_id in enumerate(nodes[1:], 1):
            prev_node = nodes[i - 1]
            connector_id = next((c for target, _, c in self._graph.get(prev_node, [])
                                 if target == node_id and c), None)
            if connector_id:
                stair = next((s for s in self._stairs if s["id"] == connector_id), None)
                if stair:
                    forward = self._stair_nodes[connector_id] == (prev_node, node_id)
                    route.append((self._node_region[prev_node],
                                  stair["bottom"] if forward else stair["top"], connector_id))
                    route.append((self._node_region[node_id],
                                  stair["top"] if forward else stair["bottom"], connector_id))
                else:
                    # Doors are horizontal portals: move to the target
                    # triangle through the portal, then continue normally.
                    target_node = self._nav_nodes[node_id]
                    route.append((target_node["region"],
                                  (target_node["center"][0], target_node["elevation"], target_node["center"][1]), connector_id))
            else:
                node = self._nav_nodes[node_id]
                route.append((node["region"],
                              (node["center"][0], node["elevation"], node["center"][1]), None))
        route.append((exit_data["region"], exit_data["position"], None))
        agent.path = route
        agent.goal_exit_id = exit_data["id"]
        agent.state = "walking" if route else "at_exit"

    def preview_navigation(self) -> dict:
        """Return routes through all currently valid calibration portals."""
        saved_states = dict(self._door_state)
        paths = []
        try:
            for door in self._doors:
                if self._door_state.get(door["id"]) not in {"locked", "fire-blocked", "blocked"}:
                    self._door_state[door["id"]] = "open"
            self._build_graph()
            for region in self._region_index.values():
                start_node = self._nearest_nav_node(
                    region["id"], (region["center"][0], region["elevation"], region["center"][1])
                )
                nodes, exit_data = self._nearest_exit_path(start_node)
                points = [(region["center"][0], region["elevation"], region["center"][1])]
                if nodes and exit_data is not None:
                    for index, node_id in enumerate(nodes[1:], 1):
                        previous = nodes[index - 1]
                        connector_id = next(
                            (connector for target, _, connector in self._graph.get(previous, [])
                             if target == node_id and connector), None)
                        if connector_id and connector_id in self._stair_nodes:
                            stair = next(s for s in self._stairs if s["id"] == connector_id)
                            forward = self._stair_nodes[connector_id] == (previous, node_id)
                            points.extend([stair["bottom"] if forward else stair["top"],
                                           stair["top"] if forward else stair["bottom"]])
                        else:
                            node = self._nav_nodes[node_id]
                            points.append((node["center"][0], node["elevation"], node["center"][1]))
                    points.append(tuple(exit_data["position"]))
                deduped = [points[0]]
                for pt in points[1:]:
                    if tuple(round(v, 4) for v in pt) != tuple(round(v, 4) for v in deduped[-1]):
                        deduped.append(pt)
                paths.append({"region": region["id"], "floor": region["floor"],
                              "start": deduped[0], "exit": exit_data["id"] if exit_data else None,
                              "reachable": bool(exit_data), "points": deduped})
            return {"schema": 2, "reachable": sum(1 for item in paths if item["reachable"]),
                    "total": len(paths), "paths": paths}
        finally:
            self._door_state = saved_states
            self._build_graph()

    def preview_smoke(self, source_floor: Optional[str] = None) -> dict:
        """Return a deterministic smoke-flow preview through floors/stairs."""
        regions = list(self._region_index.values())
        if not regions:
            return {"schema": 2, "source": None, "regions": [], "connectors": []}
        source = next((r for r in regions if source_floor and r["floor"] == source_floor), regions[0])
        adjacency = {rid: set() for rid in self._region_index}
        for left_index, left_id in enumerate(self._region_index):
            left = self._region_index[left_id]
            for right_id in list(self._region_index)[left_index + 1:]:
                right = self._region_index[right_id]
                if left["floor"] == right["floor"] and _regions_touch(left, right):
                    adjacency[left_id].add(right_id)
                    adjacency[right_id].add(left_id)
        for stair in self._stairs:
            adjacency[stair["from_region"]].add(stair["to_region"])
            adjacency[stair["to_region"]].add(stair["from_region"])
        for door in self._doors:
            adjacency[door["from_region"]].add(door["to_region"])
            adjacency[door["to_region"]].add(door["from_region"])
        distances = {source["id"]: 0}
        queue = [source["id"]]
        while queue:
            current = queue.pop(0)
            for nxt in adjacency[current]:
                if nxt not in distances:
                    distances[nxt] = distances[current] + 1
                    queue.append(nxt)
        region_points = []
        for region in regions:
            distance = distances.get(region["id"])
            region_points.append({
                "region": region["id"],
                "floor": region["floor"],
                "position": [region["center"][0], region["elevation"], region["center"][1]],
                "value": round(math.exp(-0.55 * distance) if distance is not None else 0.0, 3),
                "reachable": distance is not None,
            })
        connectors = []
        for stair in self._stairs:
            distance = distances.get(stair["from_region"])
            connectors.append({
                "id": stair["id"],
                "from_floor": stair["from_floor"],
                "to_floor": stair["to_floor"],
                "bottom": stair["bottom"],
                "top": stair["top"],
                "value": round(math.exp(-0.55 * distance) if distance is not None else 0.0, 3),
                "reachable": distance is not None,
            })
        return {
            "schema": 2,
            "source": {"region": source["id"], "floor": source["floor"],
                       "position": [source["center"][0], source["elevation"], source["center"][1]]},
            "regions": region_points,
            "connectors": connectors,
        }

    def _valid_position(self, agent: Agent3D, p: Vec3, connector_id: Optional[str]) -> bool:
        if connector_id:
            stair = next((s for s in self._stairs if s["id"] == connector_id), None)
            if stair and agent.floor_id in {stair["from_floor"], stair["to_floor"]}:
                # Keep the body in a corridor centered on the calibrated stair segment.
                horizontal_dist = min(_point_segment_distance((p[0],p[2]), (a[0],a[2]), (b[0],b[2]))[0]
                    for a,b in zip(stair["path"],stair["path"][1:]))
                return horizontal_dist <= max(.45, stair["width_m"] / 2)
        return self._region_for(p, agent.floor_id) is not None

    def _motion_geometry(self, floor_id):
        states=tuple((d["id"],self._door_state.get(d["id"])) for d in self._doors if d["floor"]==floor_id)
        cache=getattr(self,"_motion_cache",{})
        key=(floor_id,states)
        if key in cache:return cache[key]
        regions=[r for r in self._region_index.values() if r["floor"]==floor_id]
        doors=[d for d in self._doors if d["floor"]==floor_id]
        portals=[(d,door_portal(d,self._region_index)) for d in doors if self._door_passable(d["id"])]
        portals=[(d,p) for d,p in portals if p]
        edges=[]
        for region in regions:
            cuts=[p for d,p in portals if region["id"] in {d["from_region"],d["to_region"]}]
            cuts.extend(other for other in regions if other is not region
                        and not any({d["from_region"],d["to_region"]}=={other["id"],region["id"]} for d in doors))
            for a,b in _region_edges(region):
                parts=[(a,b)]
                for poly in cuts:parts=[part for x,y in parts for part in _subtract_polygon(x,y,poly)]
                edges.extend(parts)
        for _,portal in portals:
            for a,b in _region_edges(portal):
                parts=[(a,b)]
                for region in regions:parts=[part for x,y in parts for part in _subtract_polygon(x,y,region)]
                edges.extend(parts)
        # At most one cached state per floor; door changes don't grow memory.
        cache={k:v for k,v in cache.items() if k[0]!=floor_id}
        cache[key]=(regions,[p for _,p in portals],edges);self._motion_cache=cache
        return cache[key]

    def _move_human_on_floor(self, agent, delta):
        regions,portals,edges=self._motion_geometry(agent.floor_id)
        p=(agent.position[0],agent.position[2])
        inside=lambda q:any(point_in_region(q,r) for r in regions+portals)
        if not inside(p):
            # A corrupt calibration must not teleport a player across the building.
            recovered=self._nearest_walkable_region(agent.floor_id,agent.position)
            if not recovered or math.dist(p,recovered[1])>AGENT_RADIUS*2:
                agent.blocked_reason="outside-navigation";return p
            p=recovered[1]
        r=AGENT_RADIUS
        xmin,xmax=min(p[0],p[0]+delta[0])-r,max(p[0],p[0]+delta[0])+r
        zmin,zmax=min(p[1],p[1]+delta[1])-r,max(p[1],p[1]+delta[1])+r
        near=[(a,b) for a,b in edges if min(a[0],b[0])<=xmax and max(a[0],b[0])>=xmin
              and min(a[1],b[1])<=zmax and max(a[1],b[1])>=zmin]
        q=sweep_disc(p,delta,near,r)
        if not inside(q):q=p
        agent.blocked_reason="boundary" if math.dist(p,q)+1e-5<math.hypot(*delta)*.2 else ""
        for door in self.nearby_doors(agent):
            if agent.blocked_reason and door["state"]!="open":agent.blocked_reason="door-"+door["state"];break
        region=self._region_for((q[0],agent.position[1],q[1]),agent.floor_id)
        if region:
            agent.region_id=region["id"];agent.nav_node=self._nearest_nav_node(region["id"],(q[0],agent.position[1],q[1]))
        return q

    def _crowd_displacement(self, force: Vec3, dt: float, travel_m: float) -> Vec3:
        """Bound a social-force push to a fraction of the intended travel.

        Without this cap an agent surrounded by neighbours gets a push that can
        exceed its whole per-step movement, so it never reaches the 0.12 m
        arrival radius of its navigation target and the group deadlocks.  The
        cap keeps at least ``1 - CROWD_PUSH_FRACTION`` of every step directed
        along the route.
        """
        px, py, pz = force[0] * dt, force[1] * dt, force[2] * dt
        magnitude = math.sqrt(px * px + py * py + pz * pz)
        limit = max(0.0, travel_m) * CROWD_PUSH_FRACTION
        if magnitude > limit and magnitude > 1e-9:
            scale = limit / magnitude
            px, py, pz = px * scale, py * scale, pz * scale
        return px, py, pz

    def _stair_capacity(self, stair: dict) -> int:
        return int(stair.get("capacity") or max(1, min(8, math.ceil(stair["width_m"] / .6))))

    def _stair_occupancy(self, connector_id: str, forward: bool, exclude: Optional[Agent3D] = None) -> int:
        return sum(1 for other in self.agents.values()
                   if other is not exclude and not other.escaped and not other.failed
                   and other.state != "offline" and not other.connector_resume_pending
                   and other.active_connector == connector_id
                   and other.connector_forward == forward)

    def _stair_available(self, stair: dict, forward: bool, exclude: Optional[Agent3D] = None) -> bool:
        return self._stair_occupancy(stair["id"], forward, exclude) < self._stair_capacity(stair)

    def _project_stair_position(self, stair: dict, position: Vec3) -> Vec3:
        """Project a candidate onto a 3D stair centerline and width corridor.

        The horizontal footprint can collapse to a single point for a straight
        vertical connector.  Projecting only in X/Z would then pin ``t`` to
        zero and leave NPCs forever at the lower endpoint, so the primary
        projection is done on the full 3D segment; the width limit is applied
        separately in the horizontal plane.
        """
        centers = []
        for a, b in zip(stair.get("path", [stair["bottom"],stair["top"]]), stair.get("path", [stair["bottom"],stair["top"]])[1:]):
            ab = tuple(b[i] - a[i] for i in range(3))
            length2 = sum(value * value for value in ab)
            ap = tuple(position[i] - a[i] for i in range(3))
            t = max(0., min(1., sum(ap[i] * ab[i] for i in range(3)) / length2)) if length2>1e-9 else 0.
            center = tuple(a[i]+ab[i]*t for i in range(3))
            centers.append((_distance3(center, position), center))
        center = min(centers, key=lambda item:item[0])[1]
        half = max(.45, stair["width_m"] / 2)
        ox, oz = position[0] - center[0], position[2] - center[2]
        offset = math.hypot(ox, oz)
        if offset > half and offset > 1e-9:
            ox, oz = ox * half / offset, oz * half / offset
        return (center[0] + ox, center[1], center[2] + oz)

    def _crowd_force(self, agent: Agent3D) -> Vec3:
        fx = fy = fz = 0.0
        neighbours = 0
        cell=CROWD_BIN_SIZE
        gx,gy,gz=(math.floor(v/cell) for v in agent.position)
        nearby=[]
        for ix in range(gx-1,gx+2):
            for iy in range(gy-1,gy+2):
                for iz in range(gz-1,gz+2): nearby.extend(self._spatial_bins.get((ix,iy,iz),()))
        for other in nearby:
            if other is agent or other.escaped or other.failed or other.completed or other.state == "offline":
                continue
            dx, dy, dz = (agent.position[0]-other.position[0], agent.position[1]-other.position[1],
                          agent.position[2]-other.position[2])
            d = math.sqrt(dx*dx + dy*dy + dz*dz)
            if d < 1.2:
                neighbours += 1
            if d < .8 and d > 1e-5:
                force = min(2.2, (.8-d)*2.5)
                fx += dx/d*force; fy += dy/d*force; fz += dz/d*force
        agent.density = neighbours
        agent.pressure = max(0.0, neighbours * 120.0 + math.sqrt(fx*fx+fy*fy+fz*fz) * 80.0)
        return fx, fy, fz

    def _advance_human_stairs(self, agent: Agent3D, dt: float) -> bool:
        """Traverse real multi-flight stair centerlines continuously at bounded speed."""
        if not agent.active_connector:
            return False
        stair = next((s for s in self._stairs if s["id"] == agent.active_connector), None)
        if not stair:
            agent.active_connector = None
            agent.connector_entry = None
            agent.connector_resume_pending = False
            return False
        if agent.connector_resume_pending:
            if not self._stair_available(stair, agent.connector_forward, agent):
                agent.state = "queued-stairs"; agent.velocity = (0.0, 0.0, 0.0)
                return True
            agent.connector_resume_pending = False
        if agent.human and agent.input_received_at is not None and math.hypot(*agent.input) < .01:
            agent.velocity = (0., 0., 0.)
            agent.state = "walking-stairs"
            return True
        points = list(stair["path"] if agent.connector_forward else reversed(stair["path"]))
        if agent.connector_entry is not None and _distance3(agent.connector_entry, points[0]) > .001:
            points.insert(0, agent.connector_entry)
        lengths = [_distance3(a,b) for a,b in zip(points, points[1:])]
        total = max(.1, sum(lengths))
        progress = min(1., agent.connector_progress + dt * .9 * stair["speed_factor"] / total)
        distance = progress * total
        target = points[-1]
        for a,b,length in zip(points, points[1:], lengths):
            if distance <= length + 1e-9:
                t = 0 if length <= 1e-9 else distance / length
                target = tuple(a[i] + (b[i]-a[i])*t for i in range(3))
                break
            distance -= length
        previous = agent.position
        agent.position = target
        agent.velocity = tuple((target[i]-previous[i])/dt for i in range(3)) if dt>1e-9 else (0.,0.,0.)
        agent.connector_progress = progress
        agent.state = "walking-stairs"
        if progress >= 1.:
            agent.floor_id = stair["to_floor"] if agent.connector_forward else stair["from_floor"]
            agent.region_id = stair["to_region"] if agent.connector_forward else stair["from_region"]
            agent.nav_node = self._nearest_nav_node(agent.region_id, agent.position)
            agent.stair_left = (stair["id"], self.elapsed + 1.2)
            agent.active_connector = None
            agent.connector_entry = None
            agent.connector_progress = 0.
            agent.state = "walking"
        return True

    def _human_route_connector(self, agent: Agent3D) -> Optional[str]:
        """First stair on the human's current shortest route to an exit."""
        nodes, exit_data = self._nearest_exit_path(agent.nav_node)
        if exit_data is None:
            return None
        for i in range(1, len(nodes)):
            connector = next((c for target, _, c in self._graph.get(nodes[i-1], [])
                              if target == nodes[i] and c), None)
            if connector:
                return connector
        return None

    def _human_stair_at(self, agent: Agent3D, vx: float, vz: float):
        """Return ``(stair, forward)`` when a steered human enters a flight.

        Proximity alone re-captures a player who has just stepped off a flight
        and is walking away from it, and a switchback landing shares its
        horizontal footprint with the next flight, so the direction of travel
        plus the current route to the nearest exit are used to disambiguate.
        """
        if math.hypot(vx,vz)<.025:return None
        options = []
        for stair in self._stairs:
            if agent.floor_id not in {stair["from_floor"], stair["to_floor"]}:
                continue
            left = getattr(agent, "stair_left", None)
            if left and left[0] == stair["id"] and self.elapsed < left[1]:
                continue
            forward = agent.floor_id == stair["from_floor"]
            waypoints = stair["path"] if forward else list(reversed(stair["path"]))
            near, far = waypoints[:2]
            reach = max(.45, min(.7, stair["width_m"] * .5))
            if math.dist((agent.position[0], agent.position[2]),
                         (near[0], near[2])) > reach:
                continue
            dx, dz = far[0] - near[0], far[2] - near[2]
            if math.hypot(vx, vz) > 1e-3 and (dx * vx + dz * vz) <= 0:
                continue  # deliberately walking away from this flight
            options.append((stair, forward))
        if not options:
            return None
        if len(options) == 1:
            return options[0]
        wanted = self._human_route_connector(agent)
        selected = next((o for o in options if o[0]["id"] == wanted), None)
        if selected:
            return selected
        elevations = {f["id"]: f["elevation_m"] for f in self.scene["floors"]}
        return min(options, key=lambda o: elevations[
            o[0]["to_floor"] if o[1] else o[0]["from_floor"]])

    def _human_goal(self, agent: Agent3D) -> Optional[dict]:
        """Return the reached destination, distinguishing a practice goal from an exit."""
        for destination in self._exits:
            if destination["floor"] != agent.floor_id:
                continue
            ex, _, ez = destination["position"]
            radius = max(.6, destination["width_m"] * .6)
            if math.dist((agent.position[0], agent.position[2]), (ex, ez)) <= radius:
                return destination
        return None

    def _human_reached_exit(self, agent: Agent3D) -> bool:
        """Retain compatibility with callers that ask only whether a goal was reached."""
        return self._human_goal(agent) is not None

    def _finish_goal(self, agent: Agent3D):
        destination = next((e for e in self._exits if e["id"] == agent.goal_exit_id), None)
        if destination is not None and destination.get("kind") == "practice":
            agent.completed = True
            agent.state = "completed"
        else:
            agent.escaped = True
            agent.state = "escaped"
        agent.velocity = (0.0, 0.0, 0.0)

    def set_wind(self, x: float, z: float):
        """Set the horizontal wind used by the layered smoke approximation."""
        self.wind = (float(x), 0.0, float(z))

    def ignite(self, floor_id: Optional[str] = None, x: Optional[float] = None, z: Optional[float] = None) -> bool:
        choices = [r for r in self._region_index.values() if floor_id is None or r["floor"] == floor_id]
        if not choices: return False
        region = self.rng.choice(choices)
        if x is not None and z is not None:
            candidate = self._region_for((x, region["elevation"], z), region["floor"])
            if candidate is None or candidate["id"] not in {item["id"] for item in choices}:
                return False
            region = candidate
        self.fire[region["id"]] = 1.0
        node=self._nearest_nav_node(region["id"],(region["center"][0],region["elevation"],region["center"][1]))
        self.smoke[node] = max(self.smoke[node], .7)
        return True

    def step(self, dt: float):
        if self.phase != "running": return
        dt = min(max(dt, 0.0), .05)
        self.elapsed += dt
        self.real_elapsed += dt / self.time_scale
        self._update_doors()
        # Diffuse concentration over the hidden navigation graph.  The wind
        # vector biases transfer in its direction; this remains a deliberately
        # layered approximation, not a CFD/CO/FED solver.
        next_smoke = dict(self.smoke)
        wx, _, wz = self.wind
        wind_speed = math.hypot(wx, wz)
        for node_id, concentration in self.smoke.items():
            node = self._nav_nodes[node_id]
            neighbours = self._graph.get(node_id, []) + [
                (nxt, weight, connector) for nxt, weight, connector in self._smoke_links.get(node_id, [])
                if (nxt, weight, connector) not in self._graph.get(node_id, [])
            ]
            region_id = self._node_region[node_id]
            source = self.fire.get(region_id, 0.0)
            incoming = 0.0
            total_weight = 0.0
            for nxt, _, connector in neighbours:
                target = self._nav_nodes.get(nxt)
                if not target:
                    continue
                dx = target["center"][0] - node["center"][0]
                dz = target["center"][1] - node["center"][1]
                length = math.hypot(dx, dz) or 1.0
                wind_bias = 1.0
                if wind_speed > 1e-6:
                    wind_bias += .9 * max(-.5, min(1.5, (wx * dx + wz * dz) / (wind_speed * length)))
                if connector:
                    wind_bias *= .8
                    if self._door_by_id(connector) is not None and not self._door_passable(connector):
                        wind_bias *= .65
                total_weight += wind_bias
                incoming += self.smoke.get(nxt, 0.0) * wind_bias
            mean = incoming / total_weight if total_weight else concentration
            diffusion = .18 * (mean - concentration)
            source_gain = .12 * source
            next_smoke[node_id] = max(0.0, min(1.0, concentration + dt * (diffusion + source_gain)))
        self.smoke = next_smoke
        # Fire/smoke can close a door without mutating the GLB. The portal is
        # removed from the navigation graph and every affected route will be
        # replanned on the next simulation step.
        for door in self._doors:
            did = door["id"]
            if self._door_state.get(did) in {"locked", "blocked"}: continue
            region_values = [self.smoke.get(nid, 0.0) for nid, rid in self._node_region.items()
                             if rid in {door["from_region"], door["to_region"]}]
            fire_level = max([self.fire.get(door["from_region"], 0.0),
                              self.fire.get(door["to_region"], 0.0), *region_values], default=0.0)
            if fire_level >= door["fire_block_threshold"] and self._door_state.get(did) != "fire-blocked":
                self._door_state[did] = "fire-blocked"
                self._record_door_event(did, "fire-blocked", "system")
                self._build_graph()
                for a in self.agents.values(): a.path.clear()
        self._spatial_bins={}
        for candidate_agent in self.agents.values():
            if candidate_agent.escaped or candidate_agent.failed or candidate_agent.completed or candidate_agent.state == "offline": continue
            cell=CROWD_BIN_SIZE
            key=tuple(math.floor(v/cell) for v in candidate_agent.position)
            self._spatial_bins.setdefault(key,[]).append(candidate_agent)
        for agent in self.agents.values():
            if agent.escaped or agent.failed or agent.completed or agent.state == "offline": continue
            if agent.human:
                if agent.input_received_at is not None and time.monotonic()-agent.input_received_at>.5:
                    agent.input=(0.0,0.0);agent.velocity=(0.0,0.0,0.0)
                for door_info in self.nearby_doors(agent, 1.2):
                    door = self._door_by_id(door_info["id"])
                    if door and door["mode"] in {"auto", "both"} and door_info["can_open"]:
                        self.request_door(door["id"], agent, "open", "auto")
                # Stair interpolation must not skip the shared
                # smoke-dose / track-recording tail below.
                if not self._advance_human_stairs(agent, dt):
                    ux, uz = agent.input
                    vx, _, vz = agent.velocity
                    target_speed = 1.25
                    magnitude=math.hypot(ux,uz)
                    if magnitude>1:ux/=magnitude;uz/=magnitude
                    blend=1-math.exp(-dt/.14)
                    ax, az = (ux*target_speed-vx)*blend, (uz*target_speed-vz)*blend
                    fx, fy, fz = self._crowd_force(agent)
                    cx, _, cz = self._crowd_displacement((fx, fy, fz), dt, target_speed * dt)
                    vx += ax + cx; vz += az + cz
                    speed = math.hypot(vx,vz)
                    if speed > target_speed and speed > 0:
                        vx *= target_speed/speed; vz *= target_speed/speed
                    before=agent.position
                    walked=self._move_human_on_floor(agent,(vx*dt,vz*dt))
                    vx=(walked[0]-before[0])/dt;vz=(walked[1]-before[2])/dt
                    agent.position=(walked[0],before[1],walked[1])
                    selected=self._human_stair_at(agent,vx,vz)
                    if selected:
                        stair,forward=selected
                        if self._stair_available(stair,forward,agent):
                            agent.active_connector=stair["id"];agent.connector_forward=forward
                            agent.connector_progress=0.0
                            agent.connector_entry=agent.position
                            agent.state="walking-stairs"
                        else:
                            agent.state="queued-stairs";vx=vz=0.0
                    agent.velocity = (vx,0.0,vz)
                    destination = self._human_goal(agent)
                    if destination:
                        agent.goal_exit_id = destination["id"]
                        self._finish_goal(agent)
                    elif agent.active_connector: agent.state="walking-stairs"
                    elif math.hypot(vx,vz)>.05: agent.state="walking"
                    elif agent.state not in {"walking-stairs","queued-stairs","offline"}: agent.state="waiting"

            else:
                if not agent.active_connector and self._region_for(agent.position, agent.floor_id) is None:
                    recovered = self._nearest_walkable_region(agent.floor_id, agent.position)
                    if recovered and math.dist((agent.position[0], agent.position[2]), recovered[1]) <= 1.0:
                        agent.position = (recovered[1][0], recovered[0]["elevation"], recovered[1][1])
                        agent.region_id = recovered[0]["id"]
                        agent.nav_node = self._nearest_nav_node(recovered[0]["id"], agent.position)
                npc_stair_active = False
                if agent.active_connector:
                    finished = agent.active_connector
                    self._advance_human_stairs(agent, dt)
                    if not agent.active_connector:
                        removed = False
                        while agent.path and agent.path[0][2] == finished:
                            agent.path.pop(0)
                            removed = True
                        if removed and not agent.path:
                            self._finish_goal(agent)
                    npc_stair_active = bool(agent.active_connector)
                if not npc_stair_active:
                    if agent.path and any(self._node_blocked(node_id) for node_id in
                                          (self._nearest_nav_node(agent.path[0][0], agent.path[0][1]),)):
                        # Fire/smoke can invalidate a previously cached route.
                        agent.path.clear()
                    if not agent.path:
                        self._plan(agent)
                    if not agent.path:
                        continue
                    rid, target, connector_id = agent.path[0]
                    stair = next((s for s in self._stairs if s["id"] == connector_id), None) if connector_id else None
                    if stair:
                        # Walk to the end on this floor, then follow the whole
                        # switchback. Projecting onto the nearest segment drops
                        # people onto the other flight.
                        on_top = agent.floor_id == stair["to_floor"]
                        entrance = stair["top"] if on_top else stair["bottom"]
                        forward = not on_top
                        horizontal = math.dist((agent.position[0], agent.position[2]), (entrance[0], entrance[2]))
                        if horizontal > .5:
                            dx, dz = entrance[0]-agent.position[0], entrance[2]-agent.position[2]
                            dist = math.hypot(dx, dz) or 1.0
                            step_len = min(dist, 1.3*dt)
                            region = self._region_index.get(agent.region_id)
                            moved = (dx/dist*step_len, dz/dist*step_len)
                            if region:
                                walked = slide_along_region((agent.position[0], agent.position[2]), moved, region, AGENT_RADIUS)
                                agent.position = (walked[0], region["elevation"], walked[1])
                            else:
                                agent.position = (agent.position[0]+moved[0], agent.position[1], agent.position[2]+moved[1])
                            agent.velocity = (dx/dist*1.3, 0.0, dz/dist*1.3)
                            agent.state = "walking"
                            continue
                        if not self._stair_available(stair, forward, agent):
                            agent.state = "queued-stairs"
                            agent.velocity = (0.0, 0.0, 0.0)
                            continue
                        agent.active_connector = stair["id"]
                        agent.connector_forward = forward
                        agent.connector_progress = 0.0
                        agent.connector_entry = (entrance[0], entrance[1], entrance[2])
                        agent.position = agent.connector_entry
                        agent.state = "walking-stairs"
                        continue
                    door = self._door_by_id(connector_id) if connector_id else None
                    if door:
                        other = door["to_region"] if agent.region_id == door["from_region"] else door["from_region"]
                        if agent.region_id == other:
                            while agent.path and agent.path[0][2] == door["id"]:
                                agent.path.pop(0)
                            if not agent.path:
                                self._finish_goal(agent)
                            continue
                        nx, nz = door["normal"]
                        if agent.region_id == door["to_region"]:
                            nx, nz = -nx, -nz
                        nlen = math.hypot(nx, nz) or 1.0
                        nx, nz = nx/nlen, nz/nlen
                        goal = (door["position"][0] + nx*0.5, door["position"][2] + nz*0.5)
                        if not self._door_passable(door["id"]):
                            dx, dz = door["position"][0]-agent.position[0], door["position"][2]-agent.position[2]
                            dist_door = math.hypot(dx, dz) or 1.0
                            if dist_door > 0.85 and door.get("mode") in {"auto", "both"} and not door.get("locked"):
                                step = min(dist_door, 1.3*dt)
                                region = self._region_index.get(agent.region_id)
                                moved = (dx/dist_door*step, dz/dist_door*step)
                                if region:
                                    walked = slide_along_region((agent.position[0], agent.position[2]), moved, region, AGENT_RADIUS)
                                    agent.position = (walked[0], region["elevation"], walked[1])
                                agent.state = "walking"
                            elif door.get("mode") in {"auto", "both"} and not door.get("locked") and self._door_state.get(door["id"]) not in {"locked", "fire-blocked", "blocked"}:
                                self.request_door(door["id"], agent, "open", "auto")
                                agent.state = "waiting"
                                agent.velocity = (0.0, 0.0, 0.0)
                            else:
                                agent.path.clear(); agent.state = "trapped"
                            continue
                        dx, dz = goal[0]-agent.position[0], goal[1]-agent.position[2]
                        dist = math.hypot(dx, dz) or 1.0
                        step = min(dist, 1.2*dt)
                        nxt = (agent.position[0]+dx/dist*step, door["position"][1], agent.position[2]+dz/dist*step)
                        crossed = self._region_for(nxt, agent.floor_id)
                        portal = door_portal(door, self._region_index)
                        in_portal = portal is not None and point_in_region((nxt[0], nxt[2]), portal)
                        if crossed is None and not in_portal:
                            nxt = (goal[0], door["position"][1], goal[1])
                            crossed = self._region_for(nxt, agent.floor_id)
                        agent.position = nxt
                        if crossed:
                            agent.region_id = crossed["id"]
                            agent.nav_node = self._nearest_nav_node(crossed["id"], agent.position)
                        agent.state = "walking"
                        continue
                    dx, dy, dz = target[0]-agent.position[0], target[1]-agent.position[1], target[2]-agent.position[2]
                    dist = math.sqrt(dx*dx+dy*dy+dz*dz)
                    force = self._crowd_force(agent)
                    if dist < .12:
                        if connector_id:
                            stair = next((s for s in self._stairs if s["id"] == connector_id), None)
                            if stair:
                                arriving_at_bottom = math.dist(target, stair["bottom"]) <= math.dist(target, stair["top"])
                                if agent.active_connector is None:
                                    if not self._stair_available(stair, arriving_at_bottom, agent):
                                        agent.state = "queued-stairs"
                                        agent.velocity = (0.0, 0.0, 0.0)
                                        continue
                                    agent.active_connector = stair["id"]
                                    agent.connector_forward = arriving_at_bottom
                                    agent.connector_progress = 0.0
                                    agent.connector_entry = agent.position
                                    agent.state = "walking-stairs"
                                else:
                                    was_forward = agent.connector_forward
                                    agent.active_connector = None
                                    agent.connector_progress = 0.0
                                    agent.floor_id = stair["to_floor"] if was_forward else stair["from_floor"]
                                    agent.state = "walking"
                        agent.position = target; agent.region_id = rid
                        agent.nav_node=self._nearest_nav_node(rid,target)
                        if not connector_id:
                            agent.state="walking"
                        agent.path.pop(0)
                        if not agent.path:
                            self._finish_goal(agent)
                        continue
                    speed = 1.0 if connector_id else 1.4
                    speed *= max(.55, 1.0 - self.panic*.12)
                    step_len = min(dist, speed*dt)
                    # Exit markers are allowed to sit on/outside the walkable
                    # boundary.  Finish that final short segment directly rather
                    # than projecting the agent back inside forever.
                    if connector_id is None and len(agent.path) == 1:
                        agent.position = target
                        agent.floor_id = self._region_index.get(rid, {}).get("floor", agent.floor_id)
                        agent.region_id = rid
                        agent.velocity = (dx / dist * speed, dy / dist * speed, dz / dist * speed)
                        agent.path.pop(0)
                        self._finish_goal(agent)
                        continue
                    push_x, push_y, push_z = self._crowd_displacement(
                        force, dt, step_len)
                    candidate=(agent.position[0] + dx/dist*step_len + push_x,
                               agent.position[1] + dy/dist*step_len + push_y,
                               agent.position[2] + dz/dist*step_len + push_z)
                    if connector_id:
                        stair = next((s for s in self._stairs if s["id"] == connector_id), None)
                        if stair:
                            agent.position = self._project_stair_position(stair, candidate)
                            agent.state = "walking-stairs"
                        elif self._door_passable(connector_id):
                            agent.position = candidate
                            agent.state = "walking"
                        else:
                            agent.path.clear(); agent.state = "trapped"; continue
                    else:
                        region = self._region_for(candidate, agent.floor_id)
                        current_region = self._region_index.get(agent.region_id)
                        if region:
                            agent.position = candidate
                            agent.region_id = region["id"]
                            agent.nav_node = self._nearest_nav_node(region["id"], candidate)
                        else:
                            # Slide along the wall instead of freezing: the body
                            # keeps its tangent speed so a crowd pressed against a
                            # facade keeps moving toward its target and never
                            # renders inside the wall.
                            if current_region:
                                walked = slide_along_region(
                                    (agent.position[0], agent.position[2]),
                                    (candidate[0]-agent.position[0], candidate[2]-agent.position[2]),
                                    current_region, AGENT_RADIUS)
                                agent.position = (walked[0], current_region["elevation"], walked[1])
                                agent.region_id = current_region["id"]
                                agent.nav_node = self._nearest_nav_node(current_region["id"], agent.position)
                            else:
                                agent.velocity = (0.0, 0.0, 0.0)
                                continue
                    agent.velocity = (dx/dist*speed, dy/dist*speed, dz/dist*speed)
                    near_region=self._region_for(agent.position, agent.floor_id)
                    if near_region:
                        agent.region_id=near_region["id"]
                        agent.nav_node=self._nearest_nav_node(agent.region_id,agent.position)
                    agent.path_length += step_len
            region = self._region_index.get(agent.region_id)
            concentration = self.smoke.get(agent.nav_node, self.smoke.get(agent.region_id, 0.0))
            if region and agent.region_id in self.fire: concentration = 1.0
            agent.max_smoke = max(agent.max_smoke, concentration)
            agent.smoke_dose += concentration*dt
            if concentration > .85:
                agent.health = max(0.0, agent.health - (concentration-.85)*.025*dt)
            if agent.health <= 0:
                agent.failed = True; agent.state = "down"
            if len(agent.track) == 0 or self.elapsed - (agent.track[-1][0] if agent.track else -1) >= .2:
                x,y,z=agent.position
                agent.track.append([round(self.elapsed,2), round(agent.real_elapsed if hasattr(agent,'real_elapsed') else self.real_elapsed,2),
                                    round(x,3),round(y,3),round(z,3),agent.floor_id,agent.nav_node,
                                    round(agent.velocity[0],3),round(agent.velocity[1],3),round(agent.velocity[2],3),
                                    round(concentration,4),round(agent.smoke_dose,4),round(agent.health,4),round(agent.pressure,2),agent.density,
                                    agent.state,int(agent.escaped),int(agent.failed)])
        if self.elapsed >= 180 or not any(not a.escaped and not a.failed and not a.completed for a in self.agents.values()):
            self.phase = "ended"
            self.save_outputs()

    def advance(self, real_dt: float):
        if self.phase != "running": return
        self.last_wall_time = time.monotonic()
        remaining = max(0.0, real_dt*self.time_scale)
        while remaining > 1e-9 and self.phase == "running":
            dt = min(.02, remaining); self.step(dt); remaining -= dt

    def start(self):
        self.replay_invalidated = True
        if self.phase == "paused": self.phase = "running"; return
        if self.phase == "running": return
        if self.phase == "ended": self.reset()
        self.elapsed = self.real_elapsed = 0.0
        for agent in self.agents.values(): agent.track.clear(); agent.path.clear()
        self.phase = "running"

    def pause(self):
        self.phase = "paused" if self.phase == "running" else "running" if self.phase == "paused" else self.phase

    def reset(self, clear_fire: bool = False):
        self.round_revision += 1
        self.replay_invalidated = True
        self.phase = "idle"; self.elapsed = self.real_elapsed = 0.0
        self.door_events.clear()
        for d in self._doors:
            self._door_state[d["id"]] = "locked" if d.get("locked") else d.get("initial_state", "closed")
            self._door_open_until[d["id"]] = 0.0; self._door_opening_until[d["id"]] = 0.0
        self._build_graph()
        if clear_fire: self.fire.clear(); self.smoke = {rid: 0.0 for rid in self.smoke}
        for agent in self.agents.values():
            offline = agent.state == "offline"
            agent.escaped = agent.failed = agent.completed = False; agent.state = "offline" if offline else "waiting"
            agent.goal_exit_id = None
            agent.health = 1.0; agent.smoke_dose=0.0; agent.max_smoke=0.0
            agent.path.clear(); agent.track.clear(); agent.path_length=0.0
            agent.active_connector=None; agent.connector_progress=0.0; agent.connector_entry=None
            agent.connector_forward=True
            agent.connector_resume_pending=False
            agent.input=(0.0, 0.0); agent.velocity=(0.0, 0.0, 0.0)
            agent.input_received_at=None; agent.blocked_reason=""
            agent.pressure=0.0; agent.density=0
            sample = self._sample_region()
            p, region = sample["point"], sample["region"]
            agent.position = (p[0], region["elevation"], p[1]); agent.floor_id=region["floor"]; agent.region_id=region["id"]
            agent.nav_node=self._nearest_nav_node(region["id"],agent.position)

    def replay_payload(self) -> dict:
        """Return the in-memory v2 tracks for the host replay viewer."""
        agents = []
        duration = 0.0
        for agent in self.agents.values():
            samples = []
            for row in agent.track:
                duration = max(duration, float(row[0]))
                samples.append({
                    "t_sim_s": row[0], "t_real_s": row[1],
                    "x_m": row[2], "y_m": row[3], "z_m": row[4],
                    "floor_id": row[5], "nav_region": row[6],
                    "vx_mps": row[7], "vy_mps": row[8], "vz_mps": row[9],
                    "smoke": row[10], "smoke_dose": row[11],
                    "health": row[12], "pressure": row[13], "density": row[14],
                    "state": row[15], "escaped": bool(row[16]), "failed": bool(row[17]),
                })
            if samples:
                agents.append({"pid": agent.pid, "name": agent.name, "color": agent.color,
                               "human": agent.human, "samples": samples})
        return {"schema": DATA_SCHEMA, "scene_id": self.scene["id"],
                "duration": round(duration, 2), "sample_interval": .2,
                "agents": agents}

    def public_state(self) -> dict:
        agents=[]
        for a in self.agents.values():
            agents.append({"pid":a.pid,"name":a.name,"color":a.color,"human":a.human,
                           "position":{"x":round(a.position[0],3),"y":round(a.position[1],3),"z":round(a.position[2],3)},
                           "velocity":{"x":round(a.velocity[0],3),"y":round(a.velocity[1],3),"z":round(a.velocity[2],3)},
                           "floor":a.floor_id,"region":a.region_id,"nav_region":a.nav_node,
                           "connector":a.active_connector, "connector_progress":round(a.connector_progress,3),
                           "state":a.state,"blocked_reason":a.blocked_reason,"smoke":round(a.max_smoke,3),"health":round(a.health,3),
                           "smoke_dose":round(a.smoke_dose,3),"pressure":round(a.pressure,1),"density":a.density,
                           "escaped":a.escaped,"failed":a.failed,"completed":a.completed,
                           "nearby_doors": self.nearby_doors(a), "room_id": a.region_id,
                           "trapped": a.state == "trapped"})
        return {"protocol":2,"phase":self.phase,"elapsed":round(self.elapsed,2),
                "round_revision":self.round_revision,
                "real_elapsed":round(self.real_elapsed,2),"escaped":sum(a.escaped for a in self.agents.values()),
                "failed":sum(a.failed for a in self.agents.values()),
                "completed":sum(a.completed for a in self.agents.values()),
                "alive":sum(not a.escaped and not a.failed and not a.completed for a in self.agents.values()),
                "npc_count":sum(not a.human for a in self.agents.values()),
                "human_count":sum(a.human for a in self.agents.values()),
                "npc_target":self.npc_target,"npc_enabled":self.npc_target > 0,
                "simulation_mode":"mixed" if self.npc_target > 0 else "human-only",
                "panic":self.panic,"time_scale":self.time_scale,
                "scene_id":self.scene["id"],"scale_status":self.scene["scale"]["status"],
                "doors": self.door_state_payload(), "door_events": list(self.door_events[-200:]),
                "replay_available":any(bool(a.track) for a in self.agents.values()),
                "replay_duration":round(max((a.track[-1][0] for a in self.agents.values() if a.track), default=0.0), 2),
                "floors":[{"id":f["id"],"elevation_m":f["elevation_m"]} for f in self.scene["floors"]],
                "smoke":[{"region":self._node_region.get(k,k),"nav_region":k,
                          "floor":self._nav_nodes[k]["floor"],
                          "position":[self._nav_nodes[k]["center"][0],self._nav_nodes[k]["elevation"],self._nav_nodes[k]["center"][1]],
                          "value":round(v,3)} for k,v in self.smoke.items() if v>=.02],
                "fire":[{"region":rid,"floor":self._region_index[rid]["floor"],
                         "position":[self._region_index[rid]["center"][0],self._region_index[rid]["elevation"],self._region_index[rid]["center"][1]],
                         "value":v} for rid,v in self.fire.items()],"agents":agents}

    def save_outputs(self):
        self.data_dir.mkdir(parents=True,exist_ok=True)
        track_file = "evac_latest_tracks_v2.csv"
        track_rows = sum(len(agent.track) for agent in self.agents.values())
        summary={"schema":DATA_SCHEMA,"scene_id":self.scene["id"],"scale_status":self.scene["scale"]["status"],
                 "movement_model":"swept-disc-1",
                 "navigation_review_status":self.scene.get("navigation_review",{}).get("status","unspecified"),
                 "phase":self.phase,"elapsed":self.elapsed,"real_elapsed":self.real_elapsed,
                 "escaped":sum(a.escaped for a in self.agents.values()),"failed":sum(a.failed for a in self.agents.values()),
                 "completed":sum(a.completed for a in self.agents.values()),
                 "agents":len(self.agents),"floor_count":len(self.scene["floors"]),
                 "sample_interval":.2,"track_file":track_file,"track_rows":track_rows,
                 "door_events": list(self.door_events), "experiment":dict(self.experiment)}
        tracks=io.StringIO(newline="")
        writer=csv.writer(tracks); writer.writerow(["pid","t_sim_s","t_real_s","x_m","y_m","z_m","floor_id","nav_region",
            "vx_mps","vy_mps","vz_mps","smoke","smoke_dose","health","pressure","density","state","escaped","failed"])
        for agent in self.agents.values():
            for row in agent.track: writer.writerow([agent.pid,*row])
        _atomic_text(self.data_dir/"evac_latest_tracks_v2.csv",tracks.getvalue())
        _atomic_text(self.data_dir/"evac_latest_v2.json",json.dumps(summary,ensure_ascii=False))


def _atomic_text(path: Path, text: str):
    import os, tempfile
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=f".{path.name}.",suffix=".tmp",dir=path.parent)
    try:
        # mkstemp already creates owner-only files. Windows has no fchmod.
        if hasattr(os,"fchmod"):
            os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8",newline="") as f:
            fd=None
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    except Exception:
        if fd is not None:
            try: os.close(fd)
            except OSError: pass
        try: os.unlink(tmp)
        except OSError: pass
        raise
