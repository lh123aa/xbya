# -*- coding: utf-8 -*-
"""离线渲染 chibi.vrm 到 PNG，用于直观查看当前模型形状。
正交投影 + 简单太阳光 + 材质颜色平面着色。"""
import struct, json, sys, math
import numpy as np
from PIL import Image

def load_glb(path):
    d = open(path, 'rb').read()
    json_len = struct.unpack('<I', d[12:16])[0]
    j = json.loads(d[20:20+json_len].decode('utf-8'))
    bpos = 20 + json_len
    blen = struct.unpack('<I', d[bpos:bpos+4])[0]
    bin_data = d[bpos+8:bpos+8+blen]
    return j, bin_data

def read_accessor(j, bin_data, idx):
    acc = j['accessors'][idx]
    bv = j['bufferViews'][acc['bufferView']]
    off = bv.get('byteOffset', 0) + acc.get('byteOffset', 0)
    count = acc['count']
    ctype = acc['componentType']
    typ = acc['type']
    ncomp = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}[typ]
    fmt = {5126:'f',5123:'H',5125:'I',5121:'B'}[ctype]
    # glTF 一律小端
    endian = '<' if fmt in ('f','H','I','B') else '>'
    size = {'f':4,'H':2,'I':4,'B':1}[fmt]
    raw = bin_data[off:off+count*ncomp*size]
    return np.frombuffer(raw, dtype=endian+fmt).reshape(count, ncomp)

def render(path, out, yaw_deg=0.0, height=600):
    j, bin_data = load_glb(path)
    materials = j.get('materials', [])
    colors = []
    for m in materials:
        c = m.get('pbrMetallicRoughness', {}).get('baseColorFactor', [0.8,0.8,0.8,1])
        colors.append((c[0], c[1], c[2]))
    # 顶点
    positions = read_accessor(j, bin_data, 0)
    # 每个 mesh 的索引 + 材质
    yaw = math.radians(yaw_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    R = np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    # 平移角色到原点（取整体中心）
    center = positions.mean(axis=0)
    W = positions - center
    W = W @ R.T
    xs, ys, zs = W[:,0], W[:,1], W[:,2]
    # 归一化到-1..1
    ext = max(xs.max()-xs.min(), ys.max()-ys.min())
    scale = (height*0.9) / (ys.max()-ys.min())
    px = (xs - xs.min()) * scale + 40
    py = (ys.max() - ys) * scale + 40
    Wpix = np.stack([px, py, zs], axis=1)
    img = Image.new('RGB', (int(px.max()+40), int(py.max()+40)), (30,30,40))
    pix = img.load()
    H, Wdim = img.height, img.width
    zbuf = np.full((H, Wdim), -1e9)
    xmin = xs.min()
    ymax = ys.max()
    for m in j['meshes']:
        prim = m['primitives'][0]
        mat = prim.get('material', 0)
        col = colors[mat] if mat < len(colors) else (0.7,0.7,0.7)
        idx = read_accessor(j, bin_data, prim['indices'])[:,0]
        pv = Wpix  # 全局已变换顶点
        for t in range(0, len(idx), 3):
            a,b,c = idx[t], idx[t+1], idx[t+2]
            p0,p1,p2 = pv[a], pv[b], pv[c]
            n = np.cross(p1-p0, p2-p0)
            ln = np.linalg.norm(n)
            if ln<1e-9: continue
            n/=ln
            light = np.array([0.4,0.5,0.75]); light/=np.linalg.norm(light)
            lam = max(0, abs(float(n@light)))
            shade = 0.35+0.65*lam
            r,g,b = [min(255,int(255*col[i]*shade)) for i in range(3)]
            pts = [(p0[0],p0[1]),(p1[0],p1[1]),(p2[0],p2[1])]
            minx=min(p0[0],p1[0],p2[0]); maxx=max(p0[0],p1[0],p2[0])
            miny=min(p0[1],p1[1],p2[1]); maxy=max(p0[1],p1[1],p2[1])
            for yy in range(int(max(0,miny)), int(min(H,maxy+1))):
                for xx in range(int(max(0,minx)), int(min(Wdim,maxx+1))):
                    d = (p1[1]-p2[1])*(p0[0]-p2[0])+(p2[0]-p1[0])*(p0[1]-p2[1])
                    if abs(d)<1e-9: continue
                    a1 = ((p1[1]-p2[1])*(xx-p2[0])+(p2[0]-p1[0])*(yy-p2[1]))/d
                    a2 = ((p2[1]-p0[1])*(xx-p2[0])+(p0[0]-p2[0])*(yy-p2[1]))/d
                    a3 = 1-a1-a2
                    if a1<-0.001 or a2<-0.001 or a3<-0.001: continue
                    zz = a1*p0[2]+a2*p1[2]+a3*p2[2]
                    if zz>zbuf[yy,xx]:
                        zbuf[yy,xx]=zz
                        pix[xx,yy]=(int(r),int(g),int(b))
    img.save(out)
    print("Saved", out, img.size)

if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv)>1 else r'E:\程序\桌面宠物\xbya-vrm-worktree\assets\vrm\chibi.vrm'
    out = sys.argv[2] if len(sys.argv)>2 else r'E:\程序\桌面宠物\xbya-vrm-worktree\_preview_front.png'
    yaw = float(sys.argv[3]) if len(sys.argv)>3 else 0.0
    render(p, out, yaw)
