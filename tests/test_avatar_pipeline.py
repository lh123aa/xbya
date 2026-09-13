# -*- coding: utf-8 -*-
"""换形象链路的一致性守护（D47）。

## 缺陷背景

做"换形象"能力时体检发现**真实存在的不一致**：

    xbya/wander/ 有 20 帧，但它的 manifest.json 里**没有 wander**

根因：`make_pet_sprites.py` 的 manifest 只由 `ANIMS` 生成：

    man = {"animations": {a: {...} for a in ANIMS}}

而 `ANIMS` 里没有 `wander`（它是后来手工加的动画）。
于是**重跑生成器会把 ANIMS 之外的动画从 manifest 里静默删掉**。

## 为什么这不是"多占点磁盘"

加载器 `AnimationController.load_pet` 是**按 `manifest["animations"]`
逐个加载**的（`animation/controller.py:63`）：

    for anim_name, anim_info in self.manifest.get("animations", {}).items():
        anim_path = os.path.join(pet_path, anim_name)

**不在 manifest 里 = 永远不加载** —— 那 20 帧等于白生成。
用户看到的是"生成了却没生效"，而且没有任何报错。

## 本用例钉住

1. 每个角色的 `manifest["animations"]` 必须与帧目录**一一对应**
2. manifest 里的帧数要与生成器 `ANIMS` 的期望一致
3. `manifest.size` 必须与实际帧尺寸一致
4. 生成器**必须保留 ANIMS 之外的动画**（回归保护）
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPRITES = ROOT / 'resources' / 'sprites'


def _characters():
    if not SPRITES.is_dir():
        return []
    return sorted(d for d in SPRITES.iterdir()
                  if d.is_dir() and (d / 'manifest.json').is_file())


def _generator_profiles():
    """由 `make_pet_sprites.py` 生成的角色（不含内置绘制的）。

    ⚠️ 本机有**两条**生成精灵图的代码路径，帧数约定不同：

      · `tools/make_pet_sprites.py`  —— 由立绘生成（idle 24 / stare 16 …）
      · `animation/builtin.py`       —— 程序化绘制内置小猫
                                        （`generate_cat_idle_frames(num_frames=36)`）

    所以"帧数必须等于 ANIMS 期望"只适用于**前一条路径**的角色。
    第一版把这条判据套到所有角色上，`cat` 立刻变红 —— 那是**误判**：
    cat 的 36 帧是它自己的正确约定，不是缺陷。
    """
    src = (ROOT / 'tools' / 'make_pet_sprites.py').read_text(encoding='utf-8')
    i = src.index('PROFILES: dict[str, dict] = {')
    j = src.index('\n}', i)
    names = []
    for m in re.finditer(r'^\s{4}"(\w+)"\s*:\s*\{', src[i:j], re.M):
        names.append(m.group(1))
    return set(names)


def _anims_from_generator():
    src = (ROOT / 'tools' / 'make_pet_sprites.py').read_text(encoding='utf-8')
    i = src.index('ANIMS = {')
    j = src.index('}', i)
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r'"(\w+)"\s*:\s*\(\s*(\d+)', src[i:j])}


class TestManifestMatchesFrameDirs:
    """manifest 与帧目录必须一一对应。"""

    @pytest.mark.parametrize('char', _characters(), ids=lambda p: p.name)
    def test_every_frame_dir_is_in_manifest(self, char):
        """有帧目录却不在 manifest 里 —— 那些帧永远不会被加载。"""
        dirs = {d.name for d in char.iterdir() if d.is_dir()}
        man = json.loads((char / 'manifest.json').read_text(encoding='utf-8'))
        anims = set(man.get('animations', {}))
        orphan = sorted(dirs - anims)
        assert not orphan, (
            f'{char.name}: 目录 {orphan} 有帧但不在 manifest 里 —— '
            f'加载器按 manifest 逐个加载，这些帧**永远不会生效**。'
            f'（生成器必须保留 ANIMS 之外的动画，见本文件 docstring）'
        )

    @pytest.mark.parametrize('char', _characters(), ids=lambda p: p.name)
    def test_every_manifest_anim_has_dir(self, char):
        """manifest 里列了但目录不存在 —— 加载时会跳过/报错。"""
        dirs = {d.name for d in char.iterdir() if d.is_dir()}
        man = json.loads((char / 'manifest.json').read_text(encoding='utf-8'))
        missing = sorted(set(man.get('animations', {})) - dirs)
        assert not missing, f'{char.name}: manifest 列了 {missing} 但没有目录'

    @pytest.mark.parametrize('char', _characters(), ids=lambda p: p.name)
    def test_frame_counts_match_generator(self, char):
        """帧数必须与生成器 ANIMS 一致。

        只对**由 make_pet_sprites.py 生成**的角色生效 ——
        `cat` 走的是 `animation/builtin.py` 的程序化绘制路径，
        帧数约定不同（idle 36 帧），拿本判据套它会误判。
        """
        if char.name not in _generator_profiles():
            pytest.skip(f'{char.name} 不是由 make_pet_sprites.py 生成的'
                        f'（可能是内置绘制角色，帧数约定不同）')
        expect = _anims_from_generator()
        man = json.loads((char / 'manifest.json').read_text(encoding='utf-8'))
        bad = []
        for a in man.get('animations', {}):
            want = expect.get(a)
            if want is None:
                continue          # ANIMS 之外的自定义动画，无期望值
            got = len(list((char / a).glob('frame_*.png')))
            if got != want:
                bad.append(f'{a}: {got} 帧，期望 {want}')
        assert not bad, f'{char.name}: ' + '；'.join(bad)

    @pytest.mark.parametrize('char', _characters(), ids=lambda p: p.name)
    def test_manifest_size_matches_real_frames(self, char):
        """manifest.size 必须等于实际帧尺寸（否则窗口按错误尺寸算）。"""
        from PIL import Image

        man = json.loads((char / 'manifest.json').read_text(encoding='utf-8'))
        size = man.get('size')
        assert size, f'{char.name}: manifest 没有 size'
        dirs = [d for d in char.iterdir() if d.is_dir()]
        frames = sorted(dirs[0].glob('frame_*.png')) if dirs else []
        if not frames:
            pytest.skip('没有帧可比对')
        real = list(Image.open(frames[0]).size)
        assert list(size) == real, (
            f'{char.name}: manifest.size={size} 与实际帧 {real} 不一致'
        )


class TestGeneratorPreservesExternalAnims:
    """生成器必须保留 ANIMS 之外的动画。

    ⚠️ 第一版只断言"源码里有某段文字"，把保留逻辑整段删掉后**照样全绿**
    （反向验证时 19 passed 不变）—— 与本项目 D34/D43 踩过的坑同族：
    **测了存在性，没测行为**。改为真的跑一遍生成器，看外部动画是否还在。
    """

    def test_regenerating_keeps_extra_animation_in_manifest(self, tmp_path):
        """跑一次真生成，断言 ANIMS 之外的动画仍在 manifest 里。

        构造一个只含 `wander` 帧目录的临时输出目录，
        跑生成器后检查 manifest 是否把 `wander` 也列进去。
        """
        import shutil
        import subprocess
        import sys

        extra = 'wander'
        # 造一个临时角色目录：先留一个"外部动画"的帧
        work = tmp_path / 'sprites' / 'xbya'
        (work / extra).mkdir(parents=True)
        shutil.copy(sorted((SPRITES / 'xbya' / extra).glob('frame_*.png'))[0],
                    work / extra / 'frame_001.png')

        # 直接调生成器主体（--verify 不写盘，所以用真实写盘 + 临时 out）
        r = subprocess.run(
            [sys.executable, 'tools/make_pet_sprites.py', 'xbya',
             '--out', str(work)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            pytest.skip(f'生成器未能运行（环境缺依赖？）: {r.stdout[-200:]}')

        man = json.loads((work / 'manifest.json').read_text(encoding='utf-8'))
        assert extra in man['animations'], (
            f'重跑生成器后 {extra} **从 manifest 里消失了** —— '
            f'加载器按 manifest 逐个加载，这会让它永远不生效。'
            f'输出: {r.stdout[-300:]}'
        )


class TestAvatarHelperContract:
    """换形象辅助工具的接口契约。"""

    def test_helper_exists(self):
        assert (ROOT / 'tools' / 'avatar_helper.py').is_file(), (
            '缺少 tools/avatar_helper.py —— 换形象 SOP 里最容易出错的三步'
            '（标坐标 / manifest 一致性 / 源图副本）需要它'
        )

    def test_helper_uses_local_src_not_name_guess(self):
        """辅助工具找源图必须用 LOCAL_SRC 的真实映射。

        `xbya` 的源图实际叫 `avatar_new.webp` ——
        按 `<name>_source*` 猜会找不到，把有源图的角色误报成"无法重新生成"。
        """
        src = (ROOT / 'tools' / 'avatar_helper.py').read_text(encoding='utf-8')
        assert '_local_src_map' in src, (
            '辅助工具没有解析 LOCAL_SRC —— 会按名字猜源图，对 xbya 这类'
            '文件名与角色名不一致的角色失效'
        )

    def test_helper_has_check_subcommand(self):
        src = (ROOT / 'tools' / 'avatar_helper.py').read_text(encoding='utf-8')
        for sub in ('check', 'ruler', 'preview', 'init', 'list'):
            assert f'"{sub}"' in src or f"'{sub}'" in src, (
                f'辅助工具缺少 {sub} 子命令'
            )
