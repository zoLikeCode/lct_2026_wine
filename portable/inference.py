"""Selected fp32 weights + original ranking, Linux Cyrillic OCR, no PyTorch."""
from pathlib import Path
import json,time,threading
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image
from .assets import model_directory, verify_bundle
from .ranking_core import (choose_bottle,guard_label_source,focus_image,local_features,
                          TextBank,visual_channels,candidate_features,scores_for)

def session(path):
    opt=ort.SessionOptions();opt.intra_op_num_threads=2;opt.inter_op_num_threads=1
    opt.enable_cpu_mem_arena=False
    return ort.InferenceSession(str(path),sess_options=opt,providers=['CPUExecutionProvider'])

class Engine:
    def __init__(self,root=None):
        self.root=model_directory(root)
        verify_bundle(self.root)
        self.meta=json.loads((self.root/'index.json').read_text())
        self.version=self.meta['version']+'-linux-onnx-fp32-ppocr5-cyrillic'
        self.gallery=self.meta['gallery'];self.references=self.meta['references']
        self.slugs=[x['slug'] for x in self.gallery];self.catalog=self.meta['catalog']
        self.recipe=self.meta['selection']['recipe']
        self.enc=session(self.root/'encoder.onnx');self.yolo=session(self.root/'yolo.onnx')
        with np.load(self.root/'index.npz') as d:self.vec=d['vectors'];self.indices=d['reference_slug_indices']
        if len(set(self.slugs))!=len(self.slugs) or len(self.vec)!=len(self.references) or not np.isfinite(self.vec).all():
            raise ValueError('Invalid gallery index')
        self.local={}
        with np.load(self.root/'geometry.npz') as d:
            for i,r in enumerate(self.references):
                self.local[r['id']]={k:d[f'{i}_{k}'] for k in ['xy','des','shape','scales','oris','responses']}
        self.ref_ids=[[r['id'] for r in self.references if r['slug']==s] for s in self.slugs]
        self.text=TextBank(self.slugs,self.catalog,self.references,self.meta['reference_lines'])
        from rapidocr import RapidOCR,LangRec,ModelType,OCRVersion
        self.ocr=RapidOCR(params={'Rec.lang_type':LangRec.CYRILLIC,'Rec.ocr_version':OCRVersion.PPOCRV5,
            'Rec.model_type':ModelType.MOBILE,'Global.model_root_dir':str(self.root/'ocr'),
            'Det.model_path':str(self.root/'ocr/PP-OCRv6_det_small.onnx'),
            'Cls.model_path':str(self.root/'ocr/ch_ppocr_mobile_v2.0_cls_mobile.onnx'),
            'Rec.model_path':str(self.root/'ocr/cyrillic_PP-OCRv5_rec_mobile.onnx'),
            'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':1,
            'Global.max_side_len':1400,'Global.log_level':'warning'})
        self.lock=threading.Lock()

    def crop(self,image):
        w,h=image.size;r=min(640/w,640/h)
        nw,nh=round(w*r),round(h*r);dw,dh=(640-nw)%32,(640-nh)%32
        left,top=round(dw/2-.1),round(dh/2-.1)
        a=cv2.resize(np.asarray(image),(nw,nh),interpolation=cv2.INTER_LINEAR)
        a=cv2.copyMakeBorder(a,top,round(dh/2+.1),left,round(dw/2+.1),cv2.BORDER_CONSTANT,value=(114,114,114))
        pred=self.yolo.run(None,{self.yolo.get_inputs()[0].name:np.ascontiguousarray(a.transpose(2,0,1)[None],dtype=np.float32)/255})[0][0].T
        pred=pred[pred[:,4+39]>=.2]
        boxes=[]
        if len(pred):
            xywh=pred[:,:4].copy();xywh[:,:2]-=xywh[:,2:]/2
            keep=cv2.dnn.NMSBoxes(xywh.tolist(),pred[:,4+39].tolist(),.2,.7)
            for i in np.asarray(keep).reshape(-1):
                x,y,bw,bh=xywh[i];boxes.append([max(0,(x-left)/r),max(0,(y-top)/r),min(w,(x+bw-left)/r),min(h,(y+bh-top)/r)])
        full={'bbox':[0,0,w,h],'selection':'full_image_no_bottle'}
        chosen=choose_bottle(boxes,w,h)
        if chosen is None:return image,full
        x,y,x2,y2=chosen;px,py=.05*(x2-x),.03*(y2-y)
        b=[max(0,int(x-px)),max(0,int(y-py)),min(w,int(x2+px)),min(h,int(y2+py))]
        if b[2]-b[0]<20 or b[3]-b[1]<40:return image,full
        return image.crop(b),{'bbox':b,'selection':'central_bottle','detections':len(boxes)}

    def encode(self,images):
        px=np.stack([np.asarray(im.resize((256,256),Image.Resampling.BILINEAR),dtype=np.float32).transpose(2,0,1)/127.5-1 for im in images])
        return self.enc.run(None,{'pixels':px})[0]

    def predict(self,image):
        with self.lock:
            started=time.perf_counter();times={};mark=started
            bottle,info=self.crop(image);source,guard=guard_label_source(image,bottle,info)
            times['bottle_ms']=(time.perf_counter()-mark)*1000;mark=time.perf_counter()
            ocr_image=source.copy();ocr_image.thumbnail((1400,1400))
            result=self.ocr(cv2.cvtColor(np.asarray(ocr_image),cv2.COLOR_RGB2BGR))
            lines=[]
            if result.txts:
                for box,text,score in zip(result.boxes,result.txts,result.scores):
                    a=np.asarray(box);b=[a[:,0].min()/ocr_image.width,a[:,1].min()/ocr_image.height,a[:,0].max()/ocr_image.width,a[:,1].max()/ocr_image.height]
                    lines.append({'text':text,'confidence':float(score),'bbox':b})
            times['ocr_ms']=(time.perf_counter()-mark)*1000;mark=time.perf_counter()
            focus,focus_info=focus_image(source,lines)
            query=self.encode([bottle,focus]);times['encoder_ms']=(time.perf_counter()-mark)*1000;mark=time.perf_counter()
            channels=visual_channels(query,self.vec,self.indices,len(self.slugs))
            indices,features=candidate_features(channels,lines,self.text,local_features(focus),self.local,self.ref_ids)
            scores=scores_for(features,self.recipe);order=np.argsort(-scores,kind='stable')[:5]
            top=[{'slug':self.slugs[indices[i]],'score':float(scores[i]),'similarity':float(features[i,0])} for i in order]
            times['rerank_ms']=(time.perf_counter()-mark)*1000
            return {'slug':top[0]['slug'],'top5':top,'model_version':self.version,
                    'margin':top[0]['score']-top[1]['score'],'latency_ms':(time.perf_counter()-started)*1000,
                    'stage_latency_ms':times,'selected_region':info,'label_focus':focus_info,
                    'recognized_text':[l['text'] for l in lines],'ocr_status':'ppocr5_cyrillic',
                    'score_type':'ranking_score_not_probability','open_set_detection':'not_calibrated'}
