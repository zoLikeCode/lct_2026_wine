"""Export selected weights to fp32 ONNX and validate numerically."""
import sys, json, shutil, hashlib
from pathlib import Path
import numpy as np
import torch
import onnxruntime as ort
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
MVP=ROOT.parent/'wine_mvp'
sys.path.insert(0,str(MVP/'src'))
from wine_mvp.common import config
from wine_mvp.modeling import Encoder
cfg=config();cfg['device']='cpu'
enc=Encoder(cfg,'finetune');enc.vision.set_attn_implementation('eager')
class Wrapper(torch.nn.Module):
    def __init__(self):
        super().__init__();self.vision=enc.vision;self.head=enc.head
    def forward(self,pixels):return self.head(self.vision(pixel_values=pixels).pooler_output)
model=Wrapper().eval()
out=ROOT/'models';out.mkdir(exist_ok=True)
if not (out/'encoder.onnx').exists():
    torch.onnx.export(model,(torch.randn(2,3,256,256),),str(out/'encoder.onnx'),input_names=['pixels'],output_names=['embedding'],opset_version=17,dynamo=False)
session=ort.InferenceSession(str(out/'encoder.onnx'),providers=['CPUExecutionProvider'])
checks=[]
paths=list((ROOT.parent/'wine_dataset/images').glob('*/*'))
for indices in [(17,27),(39,72),(201,789)]:
    px=enc.pixels([Image.open(paths[i]).convert('RGB') for i in indices])
    with torch.no_grad():actual=model(px).numpy()
    predicted=session.run(None,{'pixels':px.numpy()})[0]
    checks.append({'max_abs_error':float(np.max(np.abs(actual-predicted))),'cosine':np.sum(actual*predicted,axis=1).tolist()})
    assert np.max(np.abs(actual-predicted))<1e-4
print('Encoder verified',checks,flush=True)
from ultralytics import YOLO
if not (out/'yolo.onnx').exists():
    tmp=out/'yolo11n.pt';shutil.copy2(MVP/'models/yolo11n.pt',tmp)
    exported=YOLO(str(tmp)).export(format='onnx',opset=17,dynamic=True,simplify=False,device='cpu',imgsz=640)
    shutil.move(exported,out/'yolo.onnx');tmp.unlink()
for name in ['index.json','index.npz','geometry.npz']:shutil.copy2(MVP/'artifacts/v2/production'/name,out/name)
info={'encoder_fp32_validation':checks,'base_version':json.loads((out/'index.json').read_text())['version'],
      'source_checkpoint_sha256':hashlib.sha256((MVP/'artifacts/runs/finetune/best.pt').read_bytes()).hexdigest(),
      'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}}
(out/'provenance.json').write_text(json.dumps(info,indent=2));print('Ready',flush=True)
