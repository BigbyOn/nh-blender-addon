"""Geometric validity and BVH ray queries used by Blender-side collision QA."""
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import tessellate_polygon


def collision_errors(bm,islands):
    errors=[]
    zero=sum(face.calc_area()<=1e-12 for face in bm.faces)
    if zero:errors.append(f'Zero-area faces: {zero}')
    faces_seen=set();duplicates=0
    for face in bm.faces:
        # Opposite faces are expected at touching independent convex parts.
        # Only the same winding represents duplicate surface geometry.
        positions=tuple(tuple(round(float(x),7) for x in v.co) for v in face.verts)
        key=min(positions[i:]+positions[:i] for i in range(len(positions)))
        if key in faces_seen:duplicates+=1
        faces_seen.add(key)
    if duplicates:errors.append(f'Duplicate faces: {duplicates}')
    zero_volume=0;nonconvex=0;intersecting=0
    for island in islands:
        verts={v for f in island for v in f.verts}
        if not verts:continue
        center=sum((v.co for v in verts),Vector())/len(verts)
        diagonal=(Vector(tuple(max(v.co[i] for v in verts) for i in range(3)))-Vector(tuple(min(v.co[i] for v in verts) for i in range(3)))).length
        # Float32 mesh coordinates and thin hull triangles need the same
        # 20-micrometre floor used by the terrain hull validator.
        eps=max(2e-5,diagonal*1e-6)
        triangles=[];indices=[];face_ids=[];vertices=[v.co.copy() for v in verts]
        lookup={v:i for i,v in enumerate(verts)}
        volume=0.
        for fi,face in enumerate(island):
            # Mesh coordinates are passed as vectors; map tessellation results
            # by exact tuples because the returned vectors may be wrappers.
            polygon=[v.co.copy() for v in face.verts]
            vertex_lookup={tuple(v.co):lookup[v] for v in face.verts}
            for tessellation in tessellate_polygon([polygon]):
                # Blender 5.2 returns indices; earlier versions return vectors.
                tri=[polygon[v] if isinstance(v,int) else v for v in tessellation]
                volume+=(tri[0]-center).dot((tri[1]-center).cross(tri[2]-center))/6
                ids=tuple(vertex_lookup[tuple(v)] for v in tri)
                triangles.append(tri);indices.append(ids);face_ids.append(fi)
        if abs(volume)<=max(1e-12,diagonal**3*1e-10):zero_volume+=1
        sign=1 if volume>=0 else -1
        if any(sign*face.normal.dot(v.co-face.verts[0].co)>eps for face in island for v in verts):nonconvex+=1
        if indices:
            tree=BVHTree.FromPolygons(vertices,indices,all_triangles=True)
            for i,j in tree.overlap(tree):
                if i>=j or face_ids[i]==face_ids[j] or set(indices[i]).intersection(indices[j]):continue
                intersecting+=1;break
    if zero_volume:errors.append(f'Zero-volume components: {zero_volume}')
    if nonconvex:errors.append(f'Non-convex components: {nonconvex}; use convex parts / Pipe Boxes')
    if intersecting:errors.append(f'Self-intersecting components: {intersecting}')
    return errors


def make_bvh_inside(triangles,epsilon):
    from .nh_geometry_audit import _RAY_DIRECTIONS, _ray_triangle_hit
    vertices=[Vector(p) for tri in triangles for p in tri]
    tree=BVHTree.FromPolygons(vertices,[(i,i+1,i+2) for i in range(0,len(vertices),3)],all_triangles=True)
    def inside(point):
        origin=Vector(point)
        nearest=tree.find_nearest(origin)
        if nearest[0] is not None and nearest[3]<=epsilon:return None
        votes=[]
        for raw in _RAY_DIRECTIONS:
            direction=Vector(raw).normalized();start=origin.copy();hits=0;ambiguous=False
            for _ in range(len(triangles)+1):
                position,normal,index,distance=tree.ray_cast(start,direction)
                if position is None:break
                hit=_ray_triangle_hit(tuple(origin),tuple(direction),triangles[index],epsilon)
                if hit is not None and hit[1]:ambiguous=True;break
                hits+=1;start=position+direction*(epsilon*4)
            else:ambiguous=True
            if not ambiguous:votes.append(bool(hits%2))
        return votes[0] if len(votes)>=2 and all(v==votes[0] for v in votes) else None
    return inside
