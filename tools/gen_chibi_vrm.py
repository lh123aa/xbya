"""
生成 Q版（Chibi）卡通少女 VRM 模型
纯 Python，无外部依赖
Chibi 比例：头身比 ~1:2，大眼小嘴，可爱风格
用法: python tools/gen_chibi_vrm.py [output_path]
"""
import struct, json, os, sys, math

GLB_MAGIC = b'glTF'
GLB_VERSION = 2
BIN_CHUNK = 0x004E4942

BONE_NAMES = [
    "hips","spine","chest","upperChest","neck","head",
    "leftEye","rightEye",
    "leftShoulder","leftUpperArm","leftLowerArm","leftHand",
    "rightShoulder","rightUpperArm","rightLowerArm","rightHand",
    "leftUpperLeg","leftLowerLeg","leftFoot","leftToes",
    "rightUpperLeg","rightLowerLeg","rightFoot","rightToes",
    "leftThumbMetacarpal","leftThumbProximal","leftThumbDistal",
    "leftIndexProximal","leftIndexIntermediate","leftIndexDistal",
    "leftMiddleProximal","leftMiddleIntermediate","leftMiddleDistal",
    "leftRingProximal","leftRingIntermediate","leftRingDistal",
    "leftLittleProximal","leftLittleIntermediate","leftLittleDistal",
    "rightThumbMetacarpal","rightThumbProximal","rightThumbDistal",
    "rightIndexProximal","rightIndexIntermediate","rightIndexDistal",
    "rightMiddleProximal","rightMiddleIntermediate","rightMiddleDistal",
    "rightRingProximal","rightRingIntermediate","rightRingDistal",
    "rightLittleProximal","rightLittleIntermediate","rightLittleDistal",
]

# 骨骼绑定：头大身小，chibi 比例
BONE_BIND = {
    "hips": [0, 0.55, 0],
    "spine": [0, 0.65, 0],
    "chest": [0, 0.75, 0],
    "upperChest": [0, 0.83, 0],
    "neck": [0, 0.90, 0],
    "head": [0, 1.00, 0],
    "leftEye": [-0.07, 1.05, 0.18],
    "rightEye": [0.07, 1.05, 0.18],
    "leftShoulder": [-0.12, 0.85, 0],
    "leftUpperArm": [-0.18, 0.80, 0],
    "leftLowerArm": [-0.22, 0.68, 0],
    "leftHand": [-0.24, 0.56, 0],
    "rightShoulder": [0.12, 0.85, 0],
    "rightUpperArm": [0.18, 0.80, 0],
    "rightLowerArm": [0.22, 0.68, 0],
    "rightHand": [0.24, 0.56, 0],
    "leftUpperLeg": [-0.07, 0.50, 0],
    "leftLowerLeg": [-0.07, 0.30, 0],
    "leftFoot": [-0.07, 0.10, 0],
    "leftToes": [-0.07, 0.02, 0.04],
    "rightUpperLeg": [0.07, 0.50, 0],
    "rightLowerLeg": [0.07, 0.30, 0],
    "rightFoot": [0.07, 0.10, 0],
    "rightToes": [0.07, 0.02, 0.04],
}
for k in BONE_NAMES:
    BONE_BIND.setdefault(k, [0, 0, 0])

BONE_PARENT = {
    "spine":"hips","chest":"spine","upperChest":"chest","neck":"upperChest","head":"neck",
    "leftEye":"head","rightEye":"head",
    "leftShoulder":"upperChest","leftUpperArm":"leftShoulder","leftLowerArm":"leftUpperArm","leftHand":"leftLowerArm",
    "rightShoulder":"upperChest","rightUpperArm":"rightShoulder","rightLowerArm":"rightUpperArm","rightHand":"rightLowerArm",
    "leftUpperLeg":"hips","leftLowerLeg":"leftUpperLeg","leftFoot":"leftLowerLeg","leftToes":"leftFoot",
    "rightUpperLeg":"hips","rightLowerLeg":"rightUpperLeg","rightFoot":"rightLowerLeg","rightToes":"rightFoot",
}


def sphere(res, r):
    pos, nor, idx = [], [], []
    for lat in range(res+1):
        for lon in range(res+1):
            phi = lat*math.pi/res; theta = lon*2*math.pi/res
            x, y, z = r*math.sin(phi)*math.cos(theta), r*math.cos(phi), r*math.sin(phi)*math.sin(theta)
            pos += [x, y, z]
            l = math.sqrt(x*x+y*y+z*z) or 1
            nor += [x/l, y/l, z/l]
    for lat in range(res):
        for lon in range(res):
            a = lat*(res+1)+lon; b = a+res+1
            idx += [a,b,a+1, b,b+1,a+1]
    return pos, nor, idx

def cylinder(res, h, r_top, r_bottom=None):
    r_b = r_bottom if r_bottom is not None else r_top
    pos, nor, idx = [], [], []
    sv = []
    for i in range(res+1):
        t = i*2*math.pi/res
        pos += [r_top*math.cos(t), h/2, r_top*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
        sv.append(len(pos)//3-1)
    for i in range(res+1):
        t = i*2*math.pi/res
        pos += [r_b*math.cos(t), -h/2, r_b*math.sin(t)]
        nx = math.cos(t); nz = math.sin(t)
        # 平滑法线过渡
        l = math.sqrt(nx*nx + (h/(r_top-r_b))*((r_b-r_top)/h)**2 + nz*nz) or 1
        nor += [nx/l, 0, nz/l] if abs(r_top-r_b) > 0.001 else [nx, 0, nz]
        sv.append(len(pos)//3-1)
    for i in range(res):
        a,b,c,d = sv[i],sv[i+1],sv[i+res+1],sv[i+res+2]
        idx += [a,c,b, b,c,d]
    tc = len(pos)//3
    pos += [0, h/2, 0]; nor += [0, 1, 0]
    for i in range(res+1):
        t=i*2*math.pi/res
        pos += [r_top*math.cos(t), h/2, r_top*math.sin(t)]
        nor += [0, 1, 0]
    for i in range(res):
        idx += [tc, tc+i+1, tc+i+2]
    bc = len(pos)//3
    pos += [0, -h/2, 0]; nor += [0, -1, 0]
    for i in range(res+1):
        t=i*2*math.pi/res
        pos += [r_b*math.cos(t), -h/2, r_b*math.sin(t)]
        nor += [0, -1, 0]
    for i in range(res):
        idx += [bc, bc+i+2, bc+i+1]
    return pos, nor, idx

def cone(res, r_bottom, r_top, h):
    pos, nor, idx = [], [], []
    for i in range(res+1):
        t = i*2*math.pi/res
        pos += [r_bottom*math.cos(t), -h/2, r_bottom*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    for i in range(res+1):
        t = i*2*math.pi/res
        pos += [r_top*math.cos(t), h/2, r_top*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    for i in range(res):
        idx += [i, i+res+1, i+1,  i+1, i+res+1, i+res+2]
    base = 2*(res+1)
    for i in range(res):
        idx += [base, base+1+i, base+2+i]
    top = base + res
    for i in range(res):
        idx += [top, top+2+i, top+1+i]
    return pos, nor, idx

def ellipse(res, rx, ry, rz):
    pos, nor, idx = [], [], []
    for lat in range(res+1):
        for lon in range(res+1):
            phi = lat*math.pi/res; theta = lon*2*math.pi/res
            x = rx*math.sin(phi)*math.cos(theta)
            y = ry*math.cos(phi); z = rz*math.sin(phi)*math.sin(theta)
            l = math.sqrt(x*x+y*y+z*z) or 1
            pos += [x, y, z]; nor += [x/l, y/l, z/l]
    for lat in range(res):
        for lon in range(res):
            a = lat*(res+1)+lon; b = a+res+1
            idx += [a,b,a+1, b,b+1,a+1]
    return pos, nor, idx

def capsule(res_h, res_r, h, r):
    """胶囊体：圆柱+两端半球"""
    pos, nor, idx = [], [], []
    # Side ring (bottom)
    for i in range(res_r+1):
        t = i*2*math.pi/res_r
        pos += [r*math.cos(t), -h/2, r*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    # Mid ring
    for i in range(res_r+1):
        t = i*2*math.pi/res_r
        pos += [r*math.cos(t), h/2, r*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    # Side triangles
    for i in range(res_r):
        idx += [i, i+res_r+1, i+1,  i+1, i+res_r+1, i+res_r+2]
    # Top cap
    tc = 2*(res_r+1)
    pos += [0, h/2+r, 0]; nor += [0, 1, 0]
    for i in range(res_r+1):
        t=i*2*math.pi/res_r
        pos += [r*math.cos(t), h/2, r*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    for i in range(res_r):
        idx += [tc, tc+1+i, tc+2+i]
    # Bottom cap
    bc = tc + res_r + 1
    pos += [0, -h/2-r, 0]; nor += [0, -1, 0]
    for i in range(res_r+1):
        t=i*2*math.pi/res_r
        pos += [r*math.cos(t), -h/2, r*math.sin(t)]
        nor += [math.cos(t), 0, math.sin(t)]
    for i in range(res_r):
        idx += [bc, bc+2+i, bc+1+i]
    return pos, nor, idx

def lathe(res, profile):
    """旋转体：profile = [(y, radius), ...] 从下到上，绕 Y 轴旋转。
    生成封闭的表面（首尾用极帽闭合）。"""
    pos, nor, idx = [], [], []
    prof = profile[:]
    # 确保首尾 radius 为 0（极帽）
    if prof[0][1] > 0.0001:
        prof.insert(0, (prof[0][0], 0.0))
    if prof[-1][1] > 0.0001:
        prof.append((prof[-1][0], 0.0))
    nrows = len(prof)
    ring_start = []
    for ri, (y, r) in enumerate(prof):
        ring_start.append(len(pos)//3)
        if r <= 0.0001:
            pos += [0, y, 0]; nor += [0, 1 if ri==0 else -1, 0]
        else:
            for i in range(res+1):
                t = i*2*math.pi/res
                pos += [r*math.cos(t), y, r*math.sin(t)]
                nor += [math.cos(t), 0, math.sin(t)]
    # 侧边四边形
    for ri in range(nrows-1):
        top_pt = prof[ri][1] <= 0.0001
        bot_pt = prof[ri+1][1] <= 0.0001
        a0 = ring_start[ri]; a1 = ring_start[ri+1]
        if top_pt and not bot_pt:  # 上一行是极点，下一行是环 → 三角形扇形
            for i in range(res):
                idx += [a0, a1+i+1, a1+i]
        elif bot_pt and not top_pt:  # 下一行是极点
            for i in range(res):
                idx += [a0+i, a1, a0+i+1]
        elif top_pt and bot_pt:
            continue
        else:  # 都是环
            for i in range(res):
                b0=a0+i; b1=a0+i+1; c0=a1+i; c1=a1+i+1
                idx += [b0,c0,b1, b1,c0,c1]
    return pos, nor, idx

def disc(res, r, depth=0.0):
    """圆盘（朝前 z 向扁的椭球），用于平面化的大眼/腮红/嘴。
    depth 越小越扁平，默认很薄；法线朝 +z。"""
    pos, nor, idx = [], [], []
    for lat in range(res+1):
        for lon in range(res+1):
            phi = lat*math.pi/res; theta = lon*2*math.pi/res
            x = r*math.sin(phi)*math.cos(theta)
            y = r*math.sin(phi)*math.sin(theta)
            z = depth*math.cos(phi)  # 朝前(z)方向薄
            l = math.sqrt(x*x+y*y+z*z) or 1
            pos += [x, y, z]; nor += [0, 0, 1]
    for lat in range(res):
        for lon in range(res):
            a = lat*(res+1)+lon; b = a+res+1
            idx += [a,b,a+1, b,b+1,a+1]
    return pos, nor, idx


def create_chibi_vrm(output_path):
    """生成 Q版卡通少女 VRM 模型"""

    # 部件列表：(名称, 生成函数, 参数, 材质ID, 偏移位置)
    # Chibi 比例：头占全身 ~45%，大眼小嘴，女性曲线
    # 分层：脸在最前(z+)，头发包后脑(z-)，五官贴脸面
    parts = [
        # === 头（圆润脸蛋，脸朝前）===
        ("head",     sphere,  (40, 0.27),  0, [0, 1.03, 0.00]),   # 大圆头
        ("face",     sphere,  (36, 0.22),  0, [0, 1.00, 0.10]),   # 脸(前凸、略小收下巴)

        # === 眼睛（平面大眼，贴脸最前，左右对称）===
        ("eye_l_w",  disc,    (18, 0.075, 0.02), 0, [-0.105, 1.06, 0.315]),   # 左眼白
        ("eye_r_w",  disc,    (18, 0.075, 0.02), 0, [0.105, 1.06, 0.315]),    # 右眼白
        ("eye_l_i",  disc,    (15, 0.060, 0.015), 3, [-0.105, 1.055, 0.330]),  # 左虹膜
        ("eye_r_i",  disc,    (15, 0.060, 0.015), 3, [0.105, 1.055, 0.330]),   # 右虹膜
        ("eye_l_p",  disc,    (11, 0.042, 0.012), 4, [-0.105, 1.055, 0.340]),  # 左瞳孔
        ("eye_r_p",  disc,    (11, 0.042, 0.012), 4, [0.105, 1.055, 0.340]),   # 右瞳孔
        ("eye_l_hi", sphere,  (8, 0.020),  0, [-0.135, 1.09, 0.348]),   # 左高光(上外)
        ("eye_r_hi", sphere,  (8, 0.020),  0, [0.135, 1.09, 0.348]),    # 右高光

        # === 眉毛（细弯，贴在眼镜上方）===
        ("brow_l",   ellipse, (8, 0.045, 0.007, 0.009), 5, [-0.105, 1.135, 0.325]),
        ("brow_r",   ellipse, (8, 0.045, 0.007, 0.009), 5, [0.105, 1.135, 0.325]),

        # === 嘴巴（微笑小嘴）===
        ("mouth",    disc,    (10, 0.028, 0.012), 7, [0, 0.955, 0.330]),

        # === 腮红 ===
        ("blush_l",  disc,    (12, 0.042, 0.012), 6, [-0.165, 1.00, 0.290]),
        ("blush_r",  disc,    (12, 0.042, 0.012), 6, [0.165, 1.00, 0.290]),

        # === 鼻子 ===
        ("nose",     sphere,  (6, 0.011),  0, [0, 1.005, 0.340]),

        # === 头发（双丸子 + 刘海 + 后发，双马尾辫）===
        ("hair_helm",sphere,  (36, 0.27),  5, [0, 1.06, -0.05]),          # 头壳(后倾包后脑)
        ("hair_fringe",ellipse,(18, 0.24, 0.12, 0.16), 5, [0, 1.12, 0.12]),  # 刘海盖额(中上)
        ("hair_back",ellipse, (18, 0.24, 0.26, 0.16), 5, [0, 0.92, -0.16]),  # 后脑发
        ("hair_sl",  ellipse, (12, 0.07, 0.20, 0.07), 5, [-0.225, 1.0, 0.08]),  # 左鬓角(前帖脸侧)
        ("hair_sr",  ellipse, (12, 0.07, 0.20, 0.07), 5, [0.225, 1.0, 0.08]),   # 右鬓角
        # 双丸子(头顶两角，大而明显)
        ("twin_l",   sphere,  (18, 0.15),  5, [-0.235, 1.30, -0.02]),
        ("twin_r",   sphere,  (18, 0.15),  5, [0.235, 1.30, -0.02]),
        # 双马尾辫(垂头侧下方，辫子节)
        ("tail_l",   lathe,   (10, [(0,0.050),(0.08,0.075),(0.18,0.065),(0.28,0.040),(0.33,0.0)]), 5, [-0.295, 1.18, -0.02]),
        ("tail_r",   lathe,   (10, [(0,0.050),(0.08,0.075),(0.18,0.065),(0.28,0.040),(0.33,0.0)]), 5, [0.295, 1.18, -0.02]),
        ("tail_l2",  lathe,   (8,  [(0,0.045),(0.07,0.062),(0.16,0.048),(0.23,0.0)]), 5, [-0.295, 0.87, -0.02]),
        ("tail_r2",  lathe,   (8,  [(0,0.045),(0.07,0.062),(0.16,0.048),(0.23,0.0)]), 5, [0.295, 0.87, -0.02]),

        # === 身体（女性曲线：胸、收腰、圆臀，一体成型，略瘦长）===
        ("body",     lathe,   (20, [(0.00,0.105),(0.05,0.15),(0.11,0.18),(0.18,0.165),
                                     (0.26,0.125),(0.35,0.135),(0.43,0.145),(0.56,0.10),
                                     (0.64,0.055),(0.68,0.02)]), 1, [0,0.40,0]),
        ("neck",     lathe,   (12, [(0.0,0.050),(0.06,0.048),(0.09,0.043),(0.11,0.0)]), 0, [0,0.86,0]),
        ("chest",    sphere,  (14, 0.065),  1, [-0.065, 0.58, 0.06]),    # 左胸
        ("chest",    sphere,  (14, 0.065),  1, [0.065, 0.58, 0.06]),     # 右胸

        # === 裙子（A字蓬裙，层次）===
        ("skirt",    lathe,   (20, [(0,0.29),(0.05,0.26),(0.10,0.21),(0.14,0.16),(0.16,0.08),(0.17,0.0)]), 2, [0,0.30,0]),
        ("skirt_r",  lathe,   (16, [(0,0.02),(0.04,0.23),(0.08,0.21),(0.12,0.15),(0.14,0.0)]), 2, [0,0.28,0]),

        # === 手臂（细，自然下垂，白色袖）===
        ("arm_lu",   capsule, (10, 8, 0.13, 0.030), 1, [-0.19, 0.68, 0]),
        ("arm_ll",   capsule, (8, 6, 0.10, 0.026), 1, [-0.225, 0.52, 0]),
        ("hand_l",   sphere,  (8, 0.036),  0, [-0.24, 0.44, 0]),
        ("arm_ru",   capsule, (10, 8, 0.13, 0.030), 1, [0.19, 0.68, 0]),
        ("arm_rl",   capsule, (8, 6, 0.10, 0.026), 1, [0.225, 0.52, 0]),
        ("hand_r",   sphere,  (8, 0.036),  0, [0.24, 0.44, 0]),

        # === 腿（细、微分开，白色长袜）===
        ("leg_lu",   capsule, (10, 8, 0.15, 0.036), 2, [-0.07, 0.22, 0]),
        ("leg_ll",   capsule, (8, 6, 0.09, 0.028),  2, [-0.07, 0.10, 0]),
        ("foot_l",   sphere,  (8, 0.042),  2, [-0.07, 0.03, 0.03]),
        ("leg_ru",   capsule, (10, 8, 0.15, 0.036), 2, [0.07, 0.22, 0]),
        ("leg_rl",   capsule, (8, 6, 0.09, 0.028),  2, [0.07, 0.10, 0]),
        ("foot_r",   sphere,  (8, 0.042),  2, [0.07, 0.03, 0.03]),

        # === 饰品：蝴蝶结 ===
        ("ribbon_l", sphere, (10, 0.06), 7, [-0.24, 1.44, 0.0]),
        ("ribbon_r", sphere, (10, 0.06), 7, [0.24, 1.44, 0.0]),
    ]

    MAT_SKIN = 0
    MAT_SHIRT = 1
    MAT_SKIRT = 2
    MAT_EYE = 3
    MAT_PUPIL = 4
    MAT_HAIR = 5
    MAT_BLUSH = 6
    MAT_RIBBON = 7

    # 修正材质 ID
    def _mat_for(name, mat):
        if name.startswith(("hair", "twin", "tail")):
            return MAT_HAIR
        if name.startswith("ribbon"):
            return MAT_RIBBON
        if name.endswith(("_hi",)) or name == "nose":
            return MAT_SKIN
        if name in ("eye_l_hi", "eye_r_hi"):
            return MAT_SKIN
        if name.endswith("_p"):  # 瞳孔
            return MAT_PUPIL
        if name.endswith("_i"):  # 虹膜
            return MAT_EYE
        if name in ("eye_l_w", "eye_r_w"):
            return MAT_SKIN
        if name in ("blush_l", "blush_r"):
            return MAT_BLUSH
        if name == "mouth":
            return MAT_RIBBON  # 红粉唇色
        if name in ("skirt", "skirt_r", "leg_lu", "leg_ll", "leg_ru", "leg_rl",
                    "foot_l", "foot_r"):
            return MAT_SKIRT
        if name in ("body", "neck", "chest"):
            return MAT_SHIRT
        return MAT_SKIN

    parts_fixed = [(name, gen, args, _mat_for(name, mat), offset) for name, gen, args, mat, offset in parts]
    parts = parts_fixed

    # ===== 收集每部分的顶点/法线/索引 =====
    part_data = []
    for name, gen, args, mat, offset in parts:
        pos, nor, idx = gen(*args)
        # 应用偏移
        for i in range(0, len(pos), 3):
            pos[i]   += offset[0]
            pos[i+1] += offset[1]
            pos[i+2] += offset[2]

        part_data.append((name, pos, nor, idx, mat))

    nv = sum(len(p)//3 for _,p,_,_,_ in part_data)

    # ===== 构建 binary buffer =====
    buf = bytearray()
    bviews = []

    def emit(data):
        align = (4 - len(buf) % 4) % 4
        buf.extend(b'\x00' * align)
        off = len(buf)
        buf.extend(data)
        bviews.append({"buffer": 0, "byteOffset": off, "byteLength": len(data)})
        return len(bviews) - 1

    all_pos = [v for _,p,_,_,_ in part_data for v in p]
    all_nor = [v for _,_,n,_,_ in part_data for v in n]
    bv_pos = emit(struct.pack('<%df' % len(all_pos), *all_pos))
    bv_nor = emit(struct.pack('<%df' % len(all_nor), *all_nor))

    bv_idx_list = []
    cum_off = 0
    for _,p,_,idx,_ in part_data:
        gidx = [v + cum_off for v in idx]
        cum_off += len(p)//3
        bv = emit(struct.pack('<%dH' % len(gidx), *gidx))
        bv_idx_list.append(bv)

    # ===== Accessors =====
    accessors = [
        {"bufferView": bv_pos, "componentType": 5126, "count": nv, "type": "VEC3",
         "max": [0.40, 1.45, 0.35], "min": [-0.40, -0.05, -0.35]},
        {"bufferView": bv_nor, "componentType": 5126, "count": nv, "type": "VEC3"},
    ]
    for bv in bv_idx_list:
        accessors.append({"bufferView": bv, "componentType": 5123, "count": len(part_data[bv_idx_list.index(bv)][3]), "type": "SCALAR"})

    # ===== Materials（按需求：粉色系 + 棕色发）=====
    materials = [
        {"name":"skin","pbrMetallicRoughness":{"baseColorFactor":[1.0,0.85,0.78,1.0],"metallicFactor":0.0,"roughnessFactor":0.7}},
        {"name":"shirt","pbrMetallicRoughness":{"baseColorFactor":[1.0,0.92,0.94,1.0],"metallicFactor":0.0,"roughnessFactor":0.5}},
        {"name":"skirt","pbrMetallicRoughness":{"baseColorFactor":[0.98,0.85,0.90,1.0],"metallicFactor":0.0,"roughnessFactor":0.6}},
        {"name":"eye","pbrMetallicRoughness":{"baseColorFactor":[0.30,0.18,0.12,1.0],"metallicFactor":0.0,"roughnessFactor":0.2}},
        {"name":"pupil","pbrMetallicRoughness":{"baseColorFactor":[0.08,0.05,0.05,1.0],"metallicFactor":0.0,"roughnessFactor":0.2}},
        {"name":"hair","pbrMetallicRoughness":{"baseColorFactor":[0.42,0.25,0.16,1.0],"metallicFactor":0.05,"roughnessFactor":0.55}},
        {"name":"blush","pbrMetallicRoughness":{"baseColorFactor":[1.0,0.60,0.60,0.5],"metallicFactor":0.0,"roughnessFactor":0.8}},
        {"name":"ribbon","pbrMetallicRoughness":{"baseColorFactor":[0.95,0.35,0.45,1.0],"metallicFactor":0.0,"roughnessFactor":0.4}},
    ]

    # ===== Meshes =====
    meshes = []
    for i, (name, pos, nor, idx, mat) in enumerate(part_data):
        meshes.append({"primitives":[{"indices":2+i,"attributes":{"POSITION":0,"NORMAL":1},"material":mat}],"name":name})

    # ===== Nodes =====
    nb = len(BONE_NAMES)
    nm = len(meshes)
    nodes = []
    for name in BONE_NAMES:
        bp = BONE_BIND.get(name, [0,0,0])
        nodes.append({"name":name,"children":[],"translation":bp,"rotation":[0,0,0,1],"scale":[1,1,1]})
    for i in range(nm):
        nodes.append({"name":part_data[i][0],"children":[],"translation":[0,0,0],"rotation":[0,0,0,1],"scale":[1,1,1],"mesh":i})
    root_idx = nb + nm
    nodes.append({"name":"Root","children":list(range(nb,root_idx)),"translation":[0,0,0],"rotation":[0,0,0,1],"scale":[1,1,1]})
    for child, parent in BONE_PARENT.items():
        if child in BONE_NAMES and parent in BONE_NAMES:
            nodes[BONE_NAMES.index(parent)]["children"].append(BONE_NAMES.index(child))

    # ===== Expressions（VRM 1.0 标准格式：extensions 下字段名为 expressions）=====
    def _expr(name, isBinary=False):
        # 标准 VRM 1.0 表达式：morphTargetBinds 关联变形目标
        return {"name": name, "isBinary": isBinary, "overrideBlink": "none",
                "overrideLookAt": "none", "overrideMouth": "none", "morphTargetBinds": []}
    expr_preset = {}
    for name in ["neutral","happy","angry","sad","surprised","relaxed",
                 "aa","ih","ou","ee","oh","blink"]:
        expr_preset[name] = _expr(name, isBinary=(name in ("aa","ih","ou","ee","oh")))

    # ===== GLTF =====
    # 注意：json.scene 是 scenes 数组的下标（0），不是 node 的索引！
    # 否则 gltf.scene 会因 scenes 越界而变成 undefined，导致加载崩溃。
    gltf = {
        "asset":{"version":"2.0","generator":"xbyaPet Chibi Girl Generator"},
        "scene":0,"scenes":[{"name":"scene","nodes":[root_idx]}],
        "nodes":nodes,"meshes":meshes,"accessors":accessors,
        "bufferViews":bviews,"buffers":[{"byteLength":len(buf)}],
        "materials":materials,
        "extensions":{
            "VRMC_vrm":{
                "specVersion":"1.0",
                "meta":{"version":"1.0.0","name":"小忆Chibi","title":"Chibi Girl","author":"xbyaPet",
                         "contactInformation":"","licenseName":"MIT",
                         "licenseUrl":"https://vrm.dev/licenses/1.0/",
                         "allowedUserName":"everyone",
                         "copyrightInformation":""},
                "humanoid":{"humanBones":{
                    n:{"node":i,"restPose":{"position":list(BONE_BIND.get(n,[0,0,0])),"rotation":[0,0,0,1]}}
                    for i,n in enumerate(BONE_NAMES)
                }},
                "expressions":{"preset":expr_preset,"custom":{}},
                "firstPerson":{"meshAnnotations":[
                    {"node":nb,"type":"auto"},{"node":nb+1,"type":"auto"},{"node":nb+2,"type":"auto"}
                ],"lookAtTypeName":"Bone"},
                "lookAt":{
                    "type":"Bone","nodeName":"head",
                    "rangeMapHorizontalInner":{"inputMaxValue":90,"outputScale":10},
                    "rangeMapHorizontalOuter":{"inputMaxValue":90,"outputScale":10},
                    "rangeMapVerticalDown":{"inputMaxValue":90,"outputScale":10},
                    "rangeMapVerticalUp":{"inputMaxValue":90,"outputScale":10},
                    "offsetFromHeadBone":[0.06,0.06,0],
                },
            },
        },
        "extensionsUsed":["VRMC_vrm"],
        "extensionsRequired":["VRMC_vrm"],
    }

    # ===== Serialize GLB =====
    js = json.dumps(gltf, ensure_ascii=False, separators=(',',':'))
    jb = js.encode('utf-8')
    jp = (4-len(jb)%4)%4
    jb += b' '*jp
    glb = bytearray()
    glb += GLB_MAGIC
    glb += struct.pack('<I', GLB_VERSION)
    glb += struct.pack('<I', 12+len(jb)+8+len(buf))
    glb += struct.pack('<I', len(jb))
    glb += struct.pack('<I', 0x4E4F534A)
    glb += jb
    glb += struct.pack('<I', len(buf))
    glb += struct.pack('<I', BIN_CHUNK)
    glb += buf

    with open(output_path, 'wb') as f:
        f.write(glb)
    total_tri = sum(len(idx)//3 for _,_,_,idx,_ in part_data)
    print("OK: %s (%d KB, %d verts, ~%d tris, %d bones, %d meshes)" % (
        output_path, len(glb)//1024, nv, total_tri, len(BONE_NAMES), len(meshes)))


def main():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_path = os.path.join(base, "assets", "vrm", "chibi.vrm")
    if len(sys.argv) > 1:
        output_path = sys.argv[1]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    create_chibi_vrm(output_path)


if __name__ == "__main__":
    main()
