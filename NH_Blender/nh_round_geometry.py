"""Geometry fitting shared by round collider tools (coordinates stay local)."""
import math
import numpy as np
from mathutils import Vector, Matrix

ROUND_AXIS_ITEMS = (
    ('AUTO','Auto from Shape','Fit the axis from actual source vertices and topology'),
    ('WORLD_Z','Scene Vertical','Align the depth axis with world Z'),
    ('X','Local X','Use the source local X axis'),
    ('Y','Local Y','Use the source local Y axis'),
    ('Z','Local Z','Use the source local Z axis'),
)

def principal_axes(points):
    values=np.asarray([tuple(p) for p in points],dtype=float)
    centered=values-values.mean(axis=0)
    eigenvalues,eigenvectors=np.linalg.eigh(centered.T@centered)
    return [Vector(eigenvectors[:,i]) for i in np.argsort(eigenvalues)[::-1]]

def profile_for_axis(points,axis):
    axis=axis.normalized()
    # Fix sign for deterministic pole ordering, including negative object scale.
    major=max(range(3),key=lambda i:abs(axis[i]))
    if axis[major]<0: axis=-axis
    seed=min((Vector((1,0,0)),Vector((0,1,0)),Vector((0,0,1))),key=lambda v:abs(v.dot(axis)))
    u=(seed-axis*seed.dot(axis)).normalized();v=axis.cross(u).normalized()
    planar=np.asarray([(p.dot(u),p.dot(v)) for p in points])
    centered=planar-planar.mean(axis=0)
    values,vectors=np.linalg.eigh(centered.T@centered)
    q=vectors[:,int(np.argmax(values))]
    u=(u*float(q[0])+v*float(q[1])).normalized();v=axis.cross(u).normalized()
    coordinates=np.asarray([(p.dot(u),p.dot(v),p.dot(axis)) for p in points])
    low=coordinates.min(axis=0);high=coordinates.max(axis=0)
    middle=(low+high)*.5;size=high-low
    radius_a,radius_b=float(size[0]*.5),float(size[1]*.5)
    if min(radius_a,radius_b)<1e-7: return None
    return dict(center=u*float(middle[0])+v*float(middle[1])+axis*float(middle[2]),
                axis_a=u,axis_b=v,depth_axis=axis,radius_a=radius_a,radius_b=radius_b,
                depth=max(float(size[2]),1e-6),edge_count=max(4,min(len(points)//2,128)))

def aligned_data(data,op):
    from .nh_collider_exp import _cylinder_profile_from_data_exp
    points=data.get('local_points') or []
    if len(points)<3: raise RuntimeError('Select at least three points for a round collider')
    mode=str(getattr(op,'round_axis','AUTO'))
    if mode=='WORLD_Z':
        axis=data['matrix_world'].to_3x3().inverted_safe()@Vector((0,0,1))
        profile=profile_for_axis(points,axis)
    elif mode in ('X','Y','Z'):
        profile=profile_for_axis(points,Vector(tuple(1 if i=='XYZ'.index(mode) else 0 for i in range(3))))
    else:
        profile=_cylinder_profile_from_data_exp(data)
    if profile is None: raise RuntimeError('Cannot determine a round axis; choose Local X/Y/Z or Scene Vertical')
    frame=Matrix.Identity(4)
    for i,key in enumerate(('axis_a','axis_b','depth_axis')):
        for j in range(3): frame[j][i]=profile[key][j]
    frame.translation=profile['center']
    inverse=frame.inverted()
    local=[inverse@p for p in points]
    low=Vector(tuple(min(p[i] for p in local) for i in range(3)))
    high=Vector(tuple(max(p[i] for p in local) for i in range(3)))
    output=dict(data)
    output.update(matrix_world=data['matrix_world']@frame,local_points=local,min=low,max=high,
                  center=(low+high)*.5,size=high-low,round_axis_indices=(0,1,2),
                  edge_vectors_local=[],face_normals_local=[],face_centers_local=[],
                  two_ring_profile=None,cylinder_axis_profile=None)
    # Explicit canonical profile prevents re-inference from swapping depth and radius.
    canonical=dict(profile,center=(low+high)*.5,axis_a=Vector((1,0,0)),axis_b=Vector((0,1,0)),depth_axis=Vector((0,0,1)))
    output['two_ring_profile']=canonical
    output['cylinder_axis_profile']=canonical
    return output,frame


def ring_bounds(points,center,axis_a,axis_b,size):
    """Match inner/outer polygon contours on rays, allowing offset ring angles."""
    ra=max(abs(size[axis_a])*.5,1e-7);rb=max(abs(size[axis_b])*.5,1e-7)
    samples=[]
    seen=set()
    for p in points:
        x,y=float(p[axis_a]-center[axis_a]),float(p[axis_b]-center[axis_b])
        key=(round(x,7),round(y,7))
        if key in seen or math.hypot(x/ra,y/rb)<.05: continue
        seen.add(key);samples.append((math.hypot(x/ra,y/rb),x,y))
    samples.sort()
    if len(samples)<6: return []
    gaps=[samples[i+1][0]-samples[i][0] for i in range(len(samples)-1)]
    cut=max(range(len(gaps)),key=gaps.__getitem__)+1
    if cut<3 or len(samples)-cut<3 or gaps[cut-1]<1e-5:
        raise RuntimeError('Pipe guide needs distinct inner and outer contours')
    contours=[]
    for group in (samples[:cut],samples[cut:]):
        contours.append(sorted([(math.atan2(y,x)%(2*math.pi),x,y) for _,x,y in group]))
    angles=sorted({round(a,10) for contour in contours for a,x,y in contour})
    def at_angle(contour,a):
        dx,dy=math.cos(a),math.sin(a)
        for i,(_,x,y) in enumerate(contour):
            _,qx,qy=contour[(i+1)%len(contour)]
            ex,ey=qx-x,qy-y
            cross=dx*ey-dy*ex
            if abs(cross)<1e-12: continue
            radius=(x*ey-y*ex)/cross
            t=(x*dy-y*dx)/cross
            if radius>0 and -1e-7<=t<=1+1e-7:
                v=Vector((0,0,0));v[axis_a]=dx*radius;v[axis_b]=dy*radius
                return v
        raise RuntimeError('Pipe contour is not a closed ring around its center')
    return [(at_angle(contours[0],a),at_angle(contours[1],a)) for a in angles]

def topology_profile(data):
    """Prefer real closed edge rings over an axis inferred from the bounds.

Both short-edge and long-edge groups are tried: axial edges can be either
longer (a pipe) or shorter (a wheel) than the circumference edges.
"""
    import bmesh
    source=data.get('source_obj')
    if source is None or getattr(source,'type',None)!='MESH':return None
    points=data.get('local_points') or []
    if len(points)<6:return None
    allowed={tuple(round(float(v),6) for v in p) for p in points}
    if source.mode=='EDIT':
        bm=bmesh.from_edit_mesh(source.data)
        bm.verts.index_update()
        verts={v.index:v.co.copy() for v in bm.verts if tuple(round(float(x),6) for x in v.co) in allowed}
        edges=[(ed.verts[0].index,ed.verts[1].index) for ed in bm.edges if all(v.index in verts for v in ed.verts)]
    else:
        verts={v.index:v.co.copy() for v in source.data.vertices if tuple(round(float(x),6) for x in v.co) in allowed}
        edges=[tuple(ed.vertices) for ed in source.data.edges if all(i in verts for i in ed.vertices)]
    edges=[edge for edge in edges if (verts[edge[1]]-verts[edge[0]]).length>1e-8]
    if not edges:return None
    lengths=[(verts[b]-verts[a]).length for a,b in edges]
    sorted_lengths=sorted(set(round(length,6) for length in lengths))
    gaps=sorted(((sorted_lengths[i+1]/max(sorted_lengths[i],1e-9), (sorted_lengths[i]+sorted_lengths[i+1])*.5) for i in range(len(sorted_lengths)-1)),reverse=True)[:8]
    masks=[list(range(len(edges)))]
    for ratio,cut in gaps:
        if ratio<1.03:continue
        masks.extend(([i for i,x in enumerate(lengths) if x<=cut],[i for i,x in enumerate(lengths) if x>cut]))
    # Handles unequal tessellation and ellipses without assuming equal edge lengths.
    for axis in principal_axes(points):
        masks.append([i for i,(a,b) in enumerate(edges) if abs((verts[b]-verts[a]).normalized().dot(axis))<.15])
    loops={}
    diagonal=(Vector(tuple(max(p[i] for p in points) for i in range(3)))-Vector(tuple(min(p[i] for p in points) for i in range(3)))).length
    for mask in masks:
        adjacency={}
        for i in mask:
            a,b=edges[i];adjacency.setdefault(a,set()).add(b);adjacency.setdefault(b,set()).add(a)
        remaining=set(adjacency)
        while remaining:
            stack=[remaining.pop()];component=set(stack)
            while stack:
                for other in adjacency[stack.pop()]:
                    if other not in component:component.add(other);remaining.discard(other);stack.append(other)
            if len(component)<4 or any(len(adjacency[i])!=2 for i in component):continue
            key=tuple(sorted(component))
            if key in loops:continue
            ring=[verts[i] for i in key];center=sum(ring,Vector())/len(ring)
            axis=principal_axes(ring)[-1]
            if max(abs((p-center).dot(axis)) for p in ring)>max(diagonal*1e-5,1e-6):continue
            loops[key]=(center,axis,len(ring))
    best=None;items=list(loops.values())
    for i,(center,axis,count) in enumerate(items):
        for other,normal,other_count in items[i+1:]:
            if abs(axis.dot(normal))<.999:continue
            delta=other-center
            axial=abs(delta.dot(axis));radial=(delta-axis*delta.dot(axis)).length
            if radial>max(diagonal*.02,1e-5):continue
            score=(min(count,other_count),count+other_count,axial)
            if best is None or score>best[0]:best=(score,axis)
    if best is None:return None
    profile=profile_for_axis(points,best[1])
    if profile is not None:profile['axis_method']='TOPOLOGY_RINGS'
    return profile


def pipe_inner_factor(radius_a, radius_b, segments, factor, thickness):
    """Keep at least thickness between homothetic polygon edges, even on ellipses."""
    factor=max(0.0,min(float(factor),.98))
    if thickness<=0:return factor
    vertices=[Vector((radius_a*math.cos(i*2*math.pi/segments),
                      radius_b*math.sin(i*2*math.pi/segments))) for i in range(segments)]
    inradius=min(abs(a.x*b.y-a.y*b.x)/(b-a).length
                 for a,b in zip(vertices,vertices[1:]+vertices[:1]))
    if thickness>=inradius:
        raise RuntimeError('Pipe Thickness closes the hole; reduce Thickness or use Cylinder')
    return min(factor,1-thickness/inradius)
