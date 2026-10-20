"""Create the original collection alcove and render responsive, empty display plates.
Run: Blender --background --factory-startup --python create_scene.py -- --output /path --quality preview
Bottle images are composed in HTML; no invented wine labels are part of the scene.
"""
import bpy, math, sys, argparse
from mathutils import Vector
from pathlib import Path
args=argparse.ArgumentParser();args.add_argument('--output',required=True);args.add_argument('--quality',default='preview');args.add_argument('--mode',default='desktop');args.add_argument('--device',default='CPU')
a=args.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
for d in list(bpy.data.materials):bpy.data.materials.remove(d)
scene=bpy.context.scene;scene.render.engine='CYCLES';scene.cycles.samples=24 if a.quality=='preview' else 96;scene.cycles.use_denoising=True
scene.cycles.max_bounces=8;scene.cycles.diffuse_bounces=4;scene.cycles.glossy_bounces=4
try:
 prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='METAL';prefs.get_devices()
 for d in prefs.devices:d.use=(d.type=='METAL' if a.device=='GPU' else d.type=='CPU')
 scene.cycles.device=a.device
except Exception as err:print('CPU fallback:',err)
scene.world.color=(.2,.2,.2);scene.world.use_nodes=True;scene.world.node_tree.nodes['Background'].inputs['Color'].default_value=(.78,.74,.67,1);scene.world.node_tree.nodes['Background'].inputs['Strength'].default_value=.3
scene.render.film_transparent=False;scene.view_settings.view_transform='AgX';scene.view_settings.look='AgX - Medium High Contrast';scene.view_settings.exposure=.3

def mat(name,color,rough=.5,metal=0):
 m=bpy.data.materials.new(name);m.use_nodes=True;p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*color,1);p.inputs['Roughness'].default_value=rough;p.inputs['Metallic'].default_value=metal;return m
stone=mat('Warm honed travertine',(.63,.52,.38),.59)
n=stone.node_tree.nodes;l=stone.node_tree.links;tex=n.new('ShaderNodeTexNoise');tex.inputs['Scale'].default_value=5;tex.inputs['Detail'].default_value=5;tex.inputs['Roughness'].default_value=.7
coord=n.new('ShaderNodeTexCoord');mapping=n.new('ShaderNodeVectorMath');mapping.operation='MULTIPLY';mapping.inputs[1].default_value=(1,3,8);l.new(coord.outputs['Generated'],mapping.inputs[0]);l.new(mapping.outputs[0],tex.inputs['Vector'])
ramp=n.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].position=.18;ramp.color_ramp.elements[0].color=(.48,.42,.34,1);ramp.color_ramp.elements[1].position=.83;ramp.color_ramp.elements[1].color=(.70,.64,.54,1);l.new(tex.outputs['Fac'],ramp.inputs[0]);l.new(ramp.outputs[0],n['Principled BSDF'].inputs['Base Color'])
pore=n.new('ShaderNodeTexNoise');pore.inputs['Scale'].default_value=170;pore.inputs['Detail'].default_value=2;l.new(coord.outputs['Generated'],pore.inputs['Vector']);bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.2;bump.inputs['Distance'].default_value=.018;l.new(pore.outputs['Fac'],bump.inputs['Height']);l.new(bump.outputs[0],n['Principled BSDF'].inputs['Normal'])
wood=mat('Quarter-sawn oak',(.19,.093,.035),.36)
n=wood.node_tree.nodes;l=wood.node_tree.links;coord=n.new('ShaderNodeTexCoord');scale=n.new('ShaderNodeVectorMath');scale.operation='MULTIPLY';scale.inputs[1].default_value=(26,26,1);l.new(coord.outputs['Generated'],scale.inputs[0]);noise=n.new('ShaderNodeTexNoise');noise.inputs['Scale'].default_value=3;noise.inputs['Detail'].default_value=3;l.new(scale.outputs[0],noise.inputs['Vector']);ramp=n.new('ShaderNodeValToRGB');ramp.color_ramp.elements[0].color=(.018,.012,.009,1);ramp.color_ramp.elements[1].color=(.055,.033,.018,1);l.new(noise.outputs['Fac'],ramp.inputs[0]);l.new(ramp.outputs[0],n['Principled BSDF'].inputs['Base Color']);bump=n.new('ShaderNodeBump');bump.inputs['Strength'].default_value=.12;bump.inputs['Distance'].default_value=.006;l.new(noise.outputs['Fac'],bump.inputs['Height']);l.new(bump.outputs[0],n['Principled BSDF'].inputs['Normal'])
bronze=mat('Brushed champagne bronze',(.39,.27,.13),.3,.82)
backmat=mat('Shadow between oak flutes',(.07,.043,.03),.8)
plaster=mat('Ivory gallery plaster',(.79,.76,.69),.83)

def cube(name,loc,size,material,bevel=.03):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);obj=bpy.context.object;obj.name=name;obj.dimensions=size;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);obj.data.materials.append(material)
 if bevel:
  mod=obj.modifiers.new('Soft machined edges','BEVEL');mod.width=bevel;mod.segments=4;mod=obj.modifiers.new('Weighted normals','WEIGHTED_NORMAL')
 return obj

def perimeter(width,height,radius,base,steps=20):
 pts=[]
 for cx,cz,start in [(width/2-radius,base+height-radius,0),(-width/2+radius,base+height-radius,90),(-width/2+radius,base+radius,180),(width/2-radius,base+radius,270)]:
  for i in range(steps+1):
   ang=math.radians(start+i*90/steps);pts.append((cx+radius*math.cos(ang),cz+radius*math.sin(ang)))
 return pts

def frame(name,w,h,r,thick,front,back,base,material):
 outer=perimeter(w,h,r,base);inner=perimeter(w-2*thick,h-2*thick,max(.08,r-thick),base+thick);N=len(outer);verts=[(x,y,z) for y in [front,back] for loop in [outer,inner] for x,z in loop];faces=[]
 for i in range(N):
  j=(i+1)%N
  faces.extend([(i,j,N+j,N+i),(2*N+j,2*N+i,3*N+i,3*N+j),(j,i,2*N+i,2*N+j),(N+i,N+j,3*N+j,3*N+i)])
 mesh=bpy.data.meshes.new(name);mesh.from_pydata(verts,[],faces);mesh.update();obj=bpy.data.objects.new(name,mesh);bpy.context.collection.objects.link(obj);obj.data.materials.append(material)
 bevel=obj.modifiers.new('Honed rim','BEVEL');bevel.width=.035;bevel.segments=4;obj.modifiers.new('Stone normals','WEIGHTED_NORMAL')
 return obj

def area(name,loc,power,color,size,target,shape='DISK',size_y=None):
 data=bpy.data.lights.new(name,'AREA');data.energy=power;data.color=color;data.shape=shape;data.size=size
 if size_y and shape=='RECTANGLE':data.size_y=size_y
 obj=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(obj);obj.location=loc;obj.rotation_euler=(Vector(target)-obj.location).to_track_quat('-Z','Y').to_euler();return obj

mobile=a.mode=='mobile';W=4.5 if mobile else 11.4;H=4.65;T=.24
# The alcove is actual volumetric joinery; all materials are procedural and editable.
cube('Wall behind alcove',(0,1.22,2.2),(W+5,.4,8),plaster,.05)
frame('Carved travertine surround',W,H,.73,T,-.77,.92,0,stone)
# Round the panel and trim flutes to the surround silhouette; no square corners peek out.
loop=perimeter(W-.24,H-.1,.68,.05);N=len(loop);verts=[(x,y,z) for y in [.94,1.09] for x,z in loop];faces=[tuple(reversed(range(N))),tuple(range(N,2*N))]+[(i,(i+1)%N,(i+1)%N+N,i+N) for i in range(N)]
mesh=bpy.data.meshes.new('Rounded back panel');mesh.from_pydata(verts,[],faces);mesh.update();panel=bpy.data.objects.new('Rounded oak back',mesh);bpy.context.collection.objects.link(panel);panel.data.materials.append(backmat)
spacing=.21;count=int((W-.18)/spacing)
for i in range(count):
 x=(i-(count-1)/2)*spacing
 r=.72;dx=max(0,abs(x)-(W/2-.14-r));trim=r-math.sqrt(max(0,r*r-dx*dx))
 cube('Oak flute %03d'%i,(x,.88,H/2),(.18,.14,H-.16-2*trim),wood,.037)
# Rounded stone base, inset bronze lip and hidden lighting.
cube('Travertine display plinth',(0,-.05,.25),(W+.12,1.95,.20),stone,.055)
cube('Bronze reveal below plinth',(0,-1.01,.135),(W-.04,.07,.035),bronze,.012)
cube('Concealed bronze light channel',(0,.65,H-.31),(W-.75,.12,.04),bronze,.015)
# Floor catches contact shadow from the architecture.
cube('Gallery floor',(0,-1.4,-.18),(W+8,9,.10),plaster,.01)
area('Large north window',(-W*.4,-5.5,7.5),950,(1,.89,.75),7,(0,0,1.5),'RECTANGLE',5)
area('Soft frontal fill',(W*.5,-7,3.2),180,(.79,.87,1),5,(0,0,2),'RECTANGLE',4)
area('Warm grazing concealed strip',(0,.37,H-.27),160 if mobile else 380,(1,.78,.47),W-.8,(0,.9,1.8),'RECTANGLE',.12)
# Off-axis sun gives the bevel and the shelf a natural, visible shadow.
data=bpy.data.lights.new('Late afternoon sun','SUN');data.energy=1.0;data.angle=math.radians(12);obj=bpy.data.objects.new('Late afternoon sun',data);bpy.context.collection.objects.link(obj);obj.rotation_euler=(math.radians(27),math.radians(-23),math.radians(-25))
camdata=bpy.data.cameras.new('Collection camera');cam=bpy.data.objects.new('Collection camera',camdata);bpy.context.collection.objects.link(cam);scene.camera=cam
camdata.type='ORTHO';camdata.ortho_scale=W+1.0
cam.location=(0,-14,5.9);target=Vector((0,0,2.05));cam.rotation_euler=(target-cam.location).to_track_quat('-Z','Y').to_euler()
scene.render.resolution_x=800 if mobile else 1800;scene.render.resolution_y=800 if mobile else 850
if a.quality=='preview':scene.render.resolution_percentage=60
else:scene.render.resolution_percentage=100
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB';scene.render.filepath=str(out/f'alcove-{a.mode}.png')
scene.render.use_file_extension=True
# Save the geometry, materials, lights, camera, and renderer settings for further art direction.
bpy.ops.wm.save_as_mainfile(filepath=str(out/f'alcove-{a.mode}.blend'))
bpy.ops.render.render(write_still=True)
# Report projected shelf contact points for precise HTML bottle placement.
from bpy_extras.object_utils import world_to_camera_view
for y in [-.6,0,.5]:
 p=world_to_camera_view(scene,cam,Vector((0,y,.35)));print('SHELF_CONTACT',y,round(p.x,4),round(1-p.y,4))
