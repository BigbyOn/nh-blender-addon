"""Topology-based geometry audit core.

This module deliberately has no Blender imports.  Blender integration converts a
LOD into world-space vertices/edges/faces and keeps the object/index mapping on
its side, while this module owns the reusable analysis result.
"""

from dataclasses import dataclass
from math import sqrt


_STRONG_NESTED_THRESHOLD = 0.95
_RAY_DIRECTIONS = (
    (1.0, 0.371, 0.217),
    (-0.163, 1.0, 0.419),
    (0.287, -0.233, 1.0),
)


def _add(left, right):
    return (left[0] + right[0], left[1] + right[1], left[2] + right[2])


def _sub(left, right):
    return (left[0] - right[0], left[1] - right[1], left[2] - right[2])


def _mul(value, scalar):
    return (value[0] * scalar, value[1] * scalar, value[2] * scalar)


def _dot(left, right):
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _length(value):
    return sqrt(max(0.0, _dot(value, value)))


def _normalized(value):
    length = _length(value)
    if length <= 1e-30:
        return (0.0, 0.0, 0.0)
    return _mul(value, 1.0 / length)


@dataclass(frozen=True)
class TopologyComponent:
    index: int
    vertex_indices: tuple
    face_indices: tuple
    triangle_indices: tuple
    aabb_min: tuple
    aabb_max: tuple
    closed: bool

    @property
    def vertices_count(self):
        return len(self.vertex_indices)

    @property
    def faces_count(self):
        return len(self.face_indices)


@dataclass(frozen=True)
class NestedComponent:
    inner_component_index: int
    outer_component_index: int
    inside_fraction: float
    inside_samples: int
    total_samples: int


@dataclass(frozen=True)
class GeometryAuditResult:
    components: tuple
    effective_components: int
    loose_vertices: tuple
    faceless_islands: tuple
    tiny_components: tuple
    nested_suspicious: tuple
    nested_strong: tuple
    safe_vertex_indices: tuple
    not_testable_pairs: int = 0

    @property
    def raw_components(self):
        return len(self.components)

    def summary(self):
        """Return stable, UI-independent counters for downstream consumers."""
        return {
            "raw_components": self.raw_components,
            "effective_components": self.effective_components,
            "loose_vertices": len(self.loose_vertices),
            "faceless_islands": len(self.faceless_islands),
            "tiny_components": len(self.tiny_components),
            "nested_suspicious": len(self.nested_suspicious),
            "nested_strong": len(self.nested_strong),
            "not_testable_pairs": self.not_testable_pairs,
        }


def _valid_index(index, vertex_count):
    return isinstance(index, int) and 0 <= index < vertex_count


def _clean_faces(faces, vertex_count):
    cleaned = []
    for face in faces or ():
        values = tuple(int(index) for index in face if _valid_index(index, vertex_count))
        if len(values) >= 3 and len(set(values)) >= 3:
            cleaned.append(values)
    return tuple(cleaned)


def _clean_edges(edges, vertex_count):
    cleaned = set()
    for edge in edges or ():
        if len(edge) < 2:
            continue
        left, right = int(edge[0]), int(edge[1])
        if left == right or not _valid_index(left, vertex_count) or not _valid_index(right, vertex_count):
            continue
        cleaned.add((min(left, right), max(left, right)))
    return tuple(sorted(cleaned))


def _fan_triangles(faces):
    triangles = []
    for face in faces:
        anchor = face[0]
        for index in range(1, len(face) - 1):
            triangles.append((anchor, face[index], face[index + 1]))
    return tuple(triangles)


def _clean_triangles(triangles, vertex_count):
    cleaned = []
    for triangle in triangles or ():
        if len(triangle) != 3:
            continue
        values = tuple(int(index) for index in triangle)
        if all(_valid_index(index, vertex_count) for index in values) and len(set(values)) == 3:
            cleaned.append(values)
    return tuple(cleaned)


def _build_adjacency(vertex_count, edges, faces):
    adjacency = [set() for _ in range(vertex_count)]
    for left, right in edges:
        adjacency[left].add(right)
        adjacency[right].add(left)
    for face in faces:
        for index, left in enumerate(face):
            right = face[(index + 1) % len(face)]
            if left == right:
                continue
            adjacency[left].add(right)
            adjacency[right].add(left)
    return adjacency


def _connected_vertex_sets(adjacency, allowed_vertices=None):
    allowed = set(range(len(adjacency))) if allowed_vertices is None else set(allowed_vertices)
    components = []
    while allowed:
        seed = min(allowed)
        allowed.remove(seed)
        stack = [seed]
        found = []
        while stack:
            vertex = stack.pop()
            found.append(vertex)
            neighbors = adjacency[vertex].intersection(allowed)
            if neighbors:
                allowed.difference_update(neighbors)
                stack.extend(neighbors)
        components.append(tuple(sorted(found)))
    return tuple(components)


def _aabb(points):
    if not points:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return (
        tuple(min(point[axis] for point in points) for axis in range(3)),
        tuple(max(point[axis] for point in points) for axis in range(3)),
    )


def _aabb_volume(component):
    extent = _sub(component.aabb_max, component.aabb_min)
    return max(0.0, extent[0]) * max(0.0, extent[1]) * max(0.0, extent[2])


def _aabb_overlaps(left, right, epsilon):
    return all(
        left.aabb_max[axis] >= right.aabb_min[axis] - epsilon
        and right.aabb_max[axis] >= left.aabb_min[axis] - epsilon
        for axis in range(3)
    )


def _point_in_aabb(point, component, epsilon):
    return all(
        component.aabb_min[axis] - epsilon <= point[axis] <= component.aabb_max[axis] + epsilon
        for axis in range(3)
    )


def _component_is_closed(face_indices, faces):
    if not face_indices:
        return False
    edge_uses = {}
    vertex_faces = {}
    for face_index in face_indices:
        face = faces[face_index]
        for vertex_index in face:
            vertex_faces.setdefault(vertex_index, set()).add(face_index)
        for index, left in enumerate(face):
            right = face[(index + 1) % len(face)]
            key = (min(left, right), max(left, right))
            edge_uses.setdefault(key, []).append(face_index)
    if not edge_uses or any(len(users) != 2 for users in edge_uses.values()):
        return False

    # Edge-manifold alone misses a bow-tie vertex where two otherwise closed
    # shells touch at exactly one vertex.  A valid closed shell has one connected
    # face fan around every vertex.
    face_neighbors = {face_index: set() for face_index in face_indices}
    for users in edge_uses.values():
        first, second = users
        face_neighbors[first].add(second)
        face_neighbors[second].add(first)

    for vertex_index, incident_faces in vertex_faces.items():
        seed = next(iter(incident_faces))
        found = {seed}
        stack = [seed]
        while stack:
            face_index = stack.pop()
            for neighbor in face_neighbors[face_index].intersection(incident_faces):
                if neighbor not in found:
                    found.add(neighbor)
                    stack.append(neighbor)
        if found != incident_faces:
            return False
    return True


def _component_samples(component, vertices, faces):
    samples = [vertices[index] for index in component.vertex_indices]
    for face_index in component.face_indices:
        face = faces[face_index]
        total = (0.0, 0.0, 0.0)
        for vertex_index in face:
            total = _add(total, vertices[vertex_index])
        samples.append(_mul(total, 1.0 / len(face)))
    return tuple(samples)


def _point_on_triangle(point, triangle, epsilon):
    first, second, third = triangle
    edge_a = _sub(second, first)
    edge_b = _sub(third, first)
    normal = _cross(edge_a, edge_b)
    normal_length = _length(normal)
    if normal_length <= epsilon * epsilon:
        return False

    relative = _sub(point, first)
    if abs(_dot(relative, normal)) / normal_length > epsilon:
        return False

    dot_aa = _dot(edge_a, edge_a)
    dot_ab = _dot(edge_a, edge_b)
    dot_bb = _dot(edge_b, edge_b)
    dot_pa = _dot(relative, edge_a)
    dot_pb = _dot(relative, edge_b)
    denominator = dot_aa * dot_bb - dot_ab * dot_ab
    if abs(denominator) <= epsilon * epsilon:
        return False
    inv = 1.0 / denominator
    u = (dot_bb * dot_pa - dot_ab * dot_pb) * inv
    v = (dot_aa * dot_pb - dot_ab * dot_pa) * inv
    barycentric_epsilon = max(1e-10, epsilon / max(_length(edge_a), _length(edge_b), epsilon))
    return u >= -barycentric_epsilon and v >= -barycentric_epsilon and u + v <= 1.0 + barycentric_epsilon


def _ray_triangle_hit(origin, direction, triangle, epsilon):
    first, second, third = triangle
    edge_a = _sub(second, first)
    edge_b = _sub(third, first)
    cross_dir = _cross(direction, edge_b)
    determinant = _dot(edge_a, cross_dir)
    if abs(determinant) <= epsilon:
        return None
    inv = 1.0 / determinant
    relative = _sub(origin, first)
    u = _dot(relative, cross_dir) * inv
    if u < -epsilon or u > 1.0 + epsilon:
        return None
    cross_rel = _cross(relative, edge_a)
    v = _dot(direction, cross_rel) * inv
    if v < -epsilon or u + v > 1.0 + epsilon:
        return None
    distance = _dot(edge_b, cross_rel) * inv
    if distance <= epsilon:
        return None
    boundary = u <= epsilon or v <= epsilon or 1.0 - u - v <= epsilon
    return distance, boundary


def _point_inside_closed_triangles(point, triangles, epsilon):
    # A point on the surface is intentionally ambiguous.  Counting it as inside
    # would turn touching/intersecting shells into confident nested candidates.
    if any(_point_on_triangle(point, triangle, epsilon) for triangle in triangles):
        return None

    votes = []
    for raw_direction in _RAY_DIRECTIONS:
        direction = _normalized(raw_direction)
        hits = []
        ambiguous = False
        for triangle in triangles:
            hit = _ray_triangle_hit(point, direction, triangle, epsilon)
            if hit is None:
                continue
            distance, boundary = hit
            if boundary:
                ambiguous = True
                break
            hits.append(distance)
        if ambiguous:
            continue

        hits.sort()
        unique_hits = []
        for distance in hits:
            if not unique_hits or abs(distance - unique_hits[-1]) > epsilon * 4.0:
                unique_hits.append(distance)
        votes.append(bool(len(unique_hits) % 2))
    if len(votes) >= 2 and all(vote == votes[0] for vote in votes[1:]):
        return votes[0]
    return None


def _nested_order(left, right, epsilon):
    left_key = (_aabb_volume(left), left.vertices_count, left.faces_count, -left.index)
    right_key = (_aabb_volume(right), right.vertices_count, right.faces_count, -right.index)
    volume_epsilon = epsilon * epsilon * epsilon
    if abs(left_key[0] - right_key[0]) <= volume_epsilon:
        left_key = (0.0, *left_key[1:])
        right_key = (0.0, *right_key[1:])
    return (left, right) if left_key <= right_key else (right, left)


def audit_mesh_geometry(vertices, edges=(), faces=(), triangles=None, inside_threshold=0.80, epsilon=None):
    """Audit one logical LOD represented in one consistent coordinate space.

    ``triangles`` may contain Blender's loop triangles for reliable concave-face
    ray tests.  If omitted, polygons are fan-triangulated (sufficient for convex
    data and the pure self-tests).
    """
    points = tuple(tuple(float(value) for value in vertex[:3]) for vertex in (vertices or ()))
    vertex_count = len(points)
    clean_faces = _clean_faces(faces, vertex_count)
    clean_edges = _clean_edges(edges, vertex_count)
    clean_triangles = _clean_triangles(
        _fan_triangles(clean_faces) if triangles is None else triangles,
        vertex_count,
    )

    if not points:
        return GeometryAuditResult((), 0, (), (), (), (), (), (), 0)

    bounds_min, bounds_max = _aabb(points)
    diagonal = _length(_sub(bounds_max, bounds_min))
    numeric_epsilon = float(epsilon) if epsilon is not None else max(1e-7, diagonal * 1e-8)
    threshold = min(_STRONG_NESTED_THRESHOLD, max(0.0, float(inside_threshold)))

    adjacency = _build_adjacency(vertex_count, clean_edges, clean_faces)
    vertex_sets = _connected_vertex_sets(adjacency)
    component_by_vertex = {}
    for component_index, vertex_indices in enumerate(vertex_sets):
        for vertex_index in vertex_indices:
            component_by_vertex[vertex_index] = component_index

    face_indices_by_component = [[] for _ in vertex_sets]
    for face_index, face in enumerate(clean_faces):
        face_indices_by_component[component_by_vertex[face[0]]].append(face_index)
    triangle_indices_by_component = [[] for _ in vertex_sets]
    for triangle_index, triangle in enumerate(clean_triangles):
        triangle_indices_by_component[component_by_vertex[triangle[0]]].append(triangle_index)

    components = []
    for component_index, vertex_indices in enumerate(vertex_sets):
        face_indices = tuple(face_indices_by_component[component_index])
        triangle_indices = tuple(triangle_indices_by_component[component_index])
        component_min, component_max = _aabb([points[index] for index in vertex_indices])
        closed = _component_is_closed(face_indices, clean_faces)
        if not triangle_indices or any(
            _length(
                _cross(
                    _sub(points[clean_triangles[index][1]], points[clean_triangles[index][0]]),
                    _sub(points[clean_triangles[index][2]], points[clean_triangles[index][0]]),
                )
            ) <= numeric_epsilon * numeric_epsilon
            for index in triangle_indices
        ):
            closed = False
        if _aabb_volume(
            TopologyComponent(0, (), (), (), component_min, component_max, closed)
        ) <= numeric_epsilon ** 3:
            closed = False
        components.append(
            TopologyComponent(
                component_index,
                tuple(vertex_indices),
                face_indices,
                triangle_indices,
                component_min,
                component_max,
                closed,
            )
        )
    components = tuple(components)

    face_vertices = {vertex_index for face in clean_faces for vertex_index in face}
    loose_vertices = tuple(index for index in range(vertex_count) if index not in face_vertices)
    faceless_islands = _connected_vertex_sets(adjacency, loose_vertices)
    tiny_components = tuple(component for component in components if component.vertices_count <= 3)

    safe_vertices = set(loose_vertices)
    for component in tiny_components:
        safe_vertices.update(component.vertex_indices)
    remaining_vertices = set(range(vertex_count)).difference(safe_vertices)
    effective_components = len(_connected_vertex_sets(adjacency, remaining_vertices))

    samples_by_component = {}
    triangles_by_component = {}
    eligible_components = [
        component
        for component in components
        if component.faces_count > 0
        and component.vertices_count > 3
        and not set(component.vertex_indices).issubset(safe_vertices)
    ]
    best_nested_by_inner = {}
    not_testable_pairs = 0

    for left_index, left in enumerate(eligible_components):
        for right in eligible_components[left_index + 1:]:
            inner, outer = _nested_order(left, right, numeric_epsilon)
            if not _aabb_overlaps(inner, outer, numeric_epsilon):
                continue

            samples = samples_by_component.get(inner.index)
            if samples is None:
                samples = _component_samples(inner, points, clean_faces)
                samples_by_component[inner.index] = samples
            if not samples:
                continue

            aabb_inside = sum(
                1 for point in samples if _point_in_aabb(point, outer, numeric_epsilon)
            )
            if aabb_inside / len(samples) < threshold:
                continue
            if not outer.closed or not outer.triangle_indices:
                not_testable_pairs += 1
                continue

            outer_triangles = triangles_by_component.get(outer.index)
            if outer_triangles is None:
                outer_triangles = tuple(
                    tuple(points[index] for index in clean_triangles[triangle_index])
                    for triangle_index in outer.triangle_indices
                )
                triangles_by_component[outer.index] = outer_triangles

            inside_count = 0
            determinate_count = 0
            for point in samples:
                if not _point_in_aabb(point, outer, numeric_epsilon):
                    determinate_count += 1
                    continue
                inside = _point_inside_closed_triangles(point, outer_triangles, numeric_epsilon)
                if inside is None:
                    continue
                determinate_count += 1
                if inside:
                    inside_count += 1

            if determinate_count == 0:
                not_testable_pairs += 1
                continue

            # Ambiguous/on-surface samples remain in the denominator.  This is
            # conservative and avoids promoting touching shells to nested.
            inside_fraction = inside_count / len(samples)
            if inside_fraction < threshold:
                continue

            match = NestedComponent(
                inner.index,
                outer.index,
                inside_fraction,
                inside_count,
                len(samples),
            )
            previous = best_nested_by_inner.get(inner.index)
            if previous is None or match.inside_fraction > previous.inside_fraction:
                best_nested_by_inner[inner.index] = match

    nested_suspicious = []
    nested_strong = []
    for match in best_nested_by_inner.values():
        if match.inside_fraction >= _STRONG_NESTED_THRESHOLD:
            nested_strong.append(match)
        else:
            nested_suspicious.append(match)

    return GeometryAuditResult(
        components=components,
        effective_components=effective_components,
        loose_vertices=loose_vertices,
        faceless_islands=faceless_islands,
        tiny_components=tiny_components,
        nested_suspicious=tuple(sorted(nested_suspicious, key=lambda item: item.inner_component_index)),
        nested_strong=tuple(sorted(nested_strong, key=lambda item: item.inner_component_index)),
        safe_vertex_indices=tuple(sorted(safe_vertices)),
        not_testable_pairs=not_testable_pairs,
    )


def _cube(center=(0.0, 0.0, 0.0), half_extent=1.0):
    cx, cy, cz = center
    h = float(half_extent)
    vertices = (
        (cx - h, cy - h, cz - h),
        (cx + h, cy - h, cz - h),
        (cx + h, cy + h, cz - h),
        (cx - h, cy + h, cz - h),
        (cx - h, cy - h, cz + h),
        (cx + h, cy - h, cz + h),
        (cx + h, cy + h, cz + h),
        (cx - h, cy + h, cz + h),
    )
    faces = (
        (0, 3, 2, 1),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (1, 2, 6, 5),
        (2, 3, 7, 6),
        (3, 0, 4, 7),
    )
    return vertices, faces


def _join_mesh_data(*items):
    vertices = []
    faces = []
    for item_vertices, item_faces in items:
        offset = len(vertices)
        vertices.extend(item_vertices)
        faces.extend(tuple(index + offset for index in face) for face in item_faces)
    return tuple(vertices), tuple(faces)


def run_geometry_audit_self_tests():
    """Run the seven minimal synthetic cases from the Geometry Audit task."""
    passed = []

    cube_vertices, cube_faces = _cube()
    result = audit_mesh_geometry(cube_vertices + ((5.0, 5.0, 5.0),), faces=cube_faces)
    assert result.loose_vertices == (8,) and len(result.faceless_islands) == 1
    passed.append("A loose vertex")

    triangle = (((4.0, 0.0, 0.0), (5.0, 0.0, 0.0), (4.0, 1.0, 0.0)), ((0, 1, 2),))
    vertices, faces = _join_mesh_data((cube_vertices, cube_faces), triangle)
    result = audit_mesh_geometry(vertices, faces=faces)
    assert len(result.tiny_components) == 1 and result.tiny_components[0].faces_count == 1
    passed.append("B tiny triangle")

    vertices, faces = _join_mesh_data(_cube(half_extent=3.0), _cube(half_extent=0.5))
    result = audit_mesh_geometry(vertices, faces=faces)
    assert len(result.nested_strong) == 1 and result.nested_strong[0].inside_fraction >= 0.99
    passed.append("C nested closed component")

    vertices, faces = _join_mesh_data(_cube(half_extent=2.0), _cube(center=(6.0, 0.0, 0.0), half_extent=0.5))
    result = audit_mesh_geometry(vertices, faces=faces)
    assert not result.nested_suspicious and not result.nested_strong
    passed.append("D outside component")

    vertices, faces = _join_mesh_data(_cube(half_extent=2.0), _cube(center=(1.8, 0.0, 0.0), half_extent=1.0))
    result = audit_mesh_geometry(vertices, faces=faces, inside_threshold=0.80)
    assert not result.nested_suspicious and not result.nested_strong
    passed.append("E overlap below threshold")

    result = audit_mesh_geometry(cube_vertices, faces=cube_faces)
    assert not result.safe_vertex_indices and result.effective_components == 1
    passed.append("F normal collision survives cleanup")

    result = audit_mesh_geometry((), edges=(), faces=())
    assert result.summary()["raw_components"] == 0 and not result.safe_vertex_indices
    passed.append("G empty mesh")

    return {"passed": len(passed), "cases": tuple(passed)}


if __name__ == "__main__":
    report = run_geometry_audit_self_tests()
    print(f"Geometry Audit self-tests passed: {report['passed']}/7")
