"""
AnimationClip + AnimationLayer + AnimationController + builtin 测试
"""

import pytest
import os
import json
import tempfile
import shutil
from pathlib import Path
from PySide6.QtGui import QImage, QPainter
from PySide6.QtCore import Qt

import sys
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from animation.clip import AnimationClip
from animation.layer import AnimationLayer
from animation.controller import AnimationController
from animation.builtin import generate_cat_idle_frames, generate_cat_frames


def _create_test_frames(directory: str, count: int, width: int = 64, height: int = 64) -> list[str]:
    """创建测试用的PNG帧文件，每帧填充不同的纯色以区分顺序"""
    os.makedirs(directory, exist_ok=True)
    paths = []
    colors = [Qt.GlobalColor.red, Qt.GlobalColor.green, Qt.GlobalColor.blue,
              Qt.GlobalColor.yellow, Qt.GlobalColor.cyan, Qt.GlobalColor.magenta,
              Qt.GlobalColor.darkRed, Qt.GlobalColor.darkGreen, Qt.GlobalColor.darkBlue,
              Qt.GlobalColor.darkYellow, Qt.GlobalColor.darkCyan, Qt.GlobalColor.darkMagenta]
    for i in range(count):
        img = QImage(width, height, QImage.Format.Format_ARGB32)
        img.fill(colors[i % len(colors)])
        path = os.path.join(directory, f"frame_{i:04d}.png")
        img.save(path)
        paths.append(path)
    return paths


class TestAnimationClip:
    """AnimationClip 测试类"""

    def setup_method(self):
        """测试前设置"""
        self.test_dir = tempfile.mkdtemp()
        self.sprite_dir = os.path.join(self.test_dir, "idle")
        _create_test_frames(self.sprite_dir, 12)

    def teardown_method(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_animation(self):
        """测试加载动画"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        assert clip.load() == True
        assert clip.get_duration() > 0

    def test_load_returns_false_for_empty_dir(self):
        """测试空目录返回False"""
        empty_dir = os.path.join(self.test_dir, "empty")
        os.makedirs(empty_dir)
        clip = AnimationClip(empty_dir, fps=12, loop=True)
        assert clip.load() == False

    def test_load_returns_false_for_nonexistent_dir(self):
        """测试不存在的目录返回False"""
        clip = AnimationClip(r"C:\nonexistent_path_12345", fps=12, loop=True)
        assert clip.load() == False

    def test_load_idempotent(self):
        """测试重复加载不会重新读取"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        assert clip.load() == True
        assert len(clip.frames) == 12
        assert clip.load() == True
        assert len(clip.frames) == 12

    def test_get_frame(self):
        """测试获取指定时间的帧"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        frame = clip.get_frame(0.0)
        assert frame is not None
        assert frame.width() > 0

    def test_get_frame_returns_image(self):
        """测试get_frame返回QImage"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        frame = clip.get_frame(0.0)
        assert isinstance(frame, QImage)

    def test_get_frame_cycling(self):
        """测试帧循环"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        frame_0 = clip.get_frame(0.0)
        frame_1 = clip.get_frame(1.0 / 12)
        assert frame_0 is not None
        assert frame_1 is not None

    def test_get_duration(self):
        """测试获取动画时长"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        duration = clip.get_duration()
        assert duration == pytest.approx(1.0, abs=0.01)

    def test_get_duration_depends_on_fps(self):
        """测试时长与fps相关"""
        clip_slow = AnimationClip(self.sprite_dir, fps=6, loop=True)
        clip_slow.load()
        clip_fast = AnimationClip(self.sprite_dir, fps=24, loop=True)
        clip_fast.load()
        assert clip_slow.get_duration() > clip_fast.get_duration()

    def test_is_finished_looping(self):
        """测试循环动画永不结束"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        assert clip.is_finished(0.0) == False
        assert clip.is_finished(100.0) == False

    def test_is_finished_non_looping(self):
        """测试非循环动画在播放结束后返回True"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=False)
        clip.load()
        assert clip.is_finished(0.0) == False
        assert clip.is_finished(clip.get_duration() + 0.1) == True

    def test_get_frame_empty_clip(self):
        """测试空clip的get_frame"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        frame = clip.get_frame(0.0)
        assert isinstance(frame, QImage)
        assert frame.width() == 0

    def test_get_frame_negative_time(self):
        """测试负时间被钳位到0，不抛异常"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        frame = clip.get_frame(-1.0)
        assert isinstance(frame, QImage)
        assert frame.width() > 0

    def test_fps_parameter(self):
        """测试fps参数正确使用"""
        clip = AnimationClip(self.sprite_dir, fps=24, loop=True)
        assert clip.fps == 24
        assert clip.frame_interval == pytest.approx(1.0 / 24, abs=0.001)

    def test_frames_sorted_by_filename(self):
        """测试帧按文件名排序加载"""
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        assert len(clip.frames) == 12

        # Verify sort order: frame_0000.png is red, frame_0011.png is darkMagenta
        colors = [Qt.GlobalColor.red, Qt.GlobalColor.green, Qt.GlobalColor.blue,
                  Qt.GlobalColor.yellow, Qt.GlobalColor.cyan, Qt.GlobalColor.magenta,
                  Qt.GlobalColor.darkRed, Qt.GlobalColor.darkGreen, Qt.GlobalColor.darkBlue,
                  Qt.GlobalColor.darkYellow, Qt.GlobalColor.darkCyan, Qt.GlobalColor.darkMagenta]
        for i, expected_color in enumerate(colors):
            pixel = clip.frames[i].pixel(0, 0)
            expected_img = QImage(1, 1, QImage.Format.Format_ARGB32)
            expected_img.fill(expected_color)
            expected_pixel = expected_img.pixel(0, 0)
            assert pixel == expected_pixel, (
                f"Frame {i} pixel {pixel:#010x} != expected {expected_pixel:#010x}"
            )

    def test_non_png_files_ignored(self):
        """测试非PNG文件被忽略"""
        # Create a non-PNG file
        with open(os.path.join(self.sprite_dir, "readme.txt"), "w") as f:
            f.write("not a png")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip.load()
        assert len(clip.frames) == 12


class TestAnimationLayer:
    """AnimationLayer 测试类"""

    def setup_method(self):
        """测试前设置"""
        self.test_dir = tempfile.mkdtemp()
        self.sprite_dir = os.path.join(self.test_dir, "idle")
        _create_test_frames(self.sprite_dir, 12)

    def teardown_method(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_init(self):
        """测试初始化"""
        layer = AnimationLayer("test")
        assert layer.name == "test"
        assert layer.clip is None
        assert layer.time == 0.0
        assert layer.active is False
        assert layer.offset_x == 0
        assert layer.offset_y == 0
        assert layer.alpha == 1.0

    def test_set_clip(self):
        """测试设置clip后激活图层"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        assert layer.clip is clip
        assert layer.time == 0.0
        assert layer.active is True

    def test_set_clip_loads_clip(self):
        """测试set_clip会调用clip.load()"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        assert clip._loaded is False
        layer.set_clip(clip)
        assert clip._loaded is True

    def test_update_advances_time(self):
        """测试update推进时间"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.update(0.1)
        assert layer.time == pytest.approx(0.1, abs=0.001)

    def test_update_looping_never_deactivates(self):
        """测试循环动画update不会停用图层"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.update(10.0)
        assert layer.active is True

    def test_update_non_looping_deactivates(self):
        """测试非循环动画播放结束后停用图层"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=False)
        layer.set_clip(clip)
        layer.update(clip.get_duration() + 0.1)
        assert layer.active is False

    def test_update_no_clip(self):
        """测试没有clip时update不报错"""
        layer = AnimationLayer("test")
        layer.update(0.1)
        assert layer.active is False
        assert layer.time == 0.0

    def test_update_inactive(self):
        """测试未激活时update不推进时间"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.active = False
        layer.update(0.1)
        assert layer.time == 0.0

    def test_render_active(self):
        """测试激活状态下render绘制帧"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)

        # Create a target image to paint onto
        target = QImage(256, 256, QImage.Format.Format_ARGB32)
        target.fill(Qt.GlobalColor.black)
        painter = QPainter(target)
        layer.render(painter, 10, 20)
        painter.end()

        # Verify something was painted (not all black)
        has_color = False
        for x in range(10, 74):
            for y in range(20, 84):
                if target.pixel(x, y) != 0xFF000000:
                    has_color = True
                    break
            if has_color:
                break
        assert has_color, "Expected painted pixels in render region"

    def test_render_inactive(self):
        """测试未激活时render不绘制"""
        layer = AnimationLayer("test")
        layer.active = False

        target = QImage(256, 256, QImage.Format.Format_ARGB32)
        target.fill(Qt.GlobalColor.black)
        painter = QPainter(target)
        layer.render(painter, 0, 0)
        painter.end()

        # All pixels should remain black
        for x in range(0, 64):
            for y in range(0, 64):
                assert target.pixel(x, y) == 0xFF000000

    def test_render_no_clip(self):
        """测试没有clip时render不绘制"""
        layer = AnimationLayer("test")
        target = QImage(256, 256, QImage.Format.Format_ARGB32)
        target.fill(Qt.GlobalColor.black)
        painter = QPainter(target)
        layer.render(painter, 0, 0)
        painter.end()

        for x in range(0, 64):
            for y in range(0, 64):
                assert target.pixel(x, y) == 0xFF000000

    def test_render_with_offset(self):
        """测试render应用偏移"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.offset_x = 10
        layer.offset_y = 20

        target = QImage(256, 256, QImage.Format.Format_ARGB32)
        target.fill(Qt.GlobalColor.black)
        painter = QPainter(target)
        layer.render(painter, 0, 0)
        painter.end()

        # Verify paint was applied at offset position
        has_color = False
        for x in range(10, 74):
            for y in range(20, 84):
                if target.pixel(x, y) != 0xFF000000:
                    has_color = True
                    break
            if has_color:
                break
        assert has_color, "Expected painted pixels at offset position"

    def test_render_with_alpha(self):
        """测试render应用透明度（RGB混合而非alpha通道）"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.alpha = 0.5

        target = QImage(256, 256, QImage.Format.Format_ARGB32)
        target.fill(Qt.GlobalColor.black)
        painter = QPainter(target)
        layer.render(painter, 0, 0)
        painter.end()

        # With alpha=0.5 on opaque black dest, result alpha is still 0xFF.
        # The blend shows in RGB: e.g. red(0xFF,0,0) blended 50% with black(0,0,0) = (0x80,0,0).
        # Verify the pixel is NOT pure source color (proves blending happened).
        pixel = target.pixel(0, 0)
        r = (pixel >> 16) & 0xFF
        # Red frame at 50% opacity on black → r should be ~0x80, not 0xFF
        assert 0 < r < 0xFF, f"Expected blended red channel, got {r:#04x}"

    def test_is_active(self):
        """测试is_active返回active状态"""
        layer = AnimationLayer("test")
        assert layer.is_active() is False
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        assert layer.is_active() is True

    def test_set_clip_resets_time(self):
        """测试set_clip重置时间"""
        layer = AnimationLayer("test")
        clip = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip)
        layer.update(0.5)
        assert layer.time > 0.0

        clip2 = AnimationClip(self.sprite_dir, fps=12, loop=True)
        layer.set_clip(clip2)
        assert layer.time == 0.0

    def test_replace_clip(self):
        """测试替换clip"""
        layer = AnimationLayer("test")
        clip1 = AnimationClip(self.sprite_dir, fps=12, loop=True)
        clip2 = AnimationClip(self.sprite_dir, fps=6, loop=False)
        layer.set_clip(clip1)
        assert layer.clip is clip1
        layer.set_clip(clip2)
        assert layer.clip is clip2


def _create_pet_dir(root: str, name: str = "cat", size=None,
                    animations=None) -> str:
    """创建测试宠物目录结构（manifest + 每个动画的帧）"""
    pet_dir = os.path.join(root, name)
    os.makedirs(pet_dir, exist_ok=True)
    if animations is None:
        animations = {"idle": {"fps": 12, "loop": True}}
    if size is None:
        size = [128, 128]
    manifest = {"name": "test", "size": size, "animations": animations}
    with open(os.path.join(pet_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    for anim_name in animations:
        _create_test_frames(os.path.join(pet_dir, anim_name), 12)
    return pet_dir


class TestAnimationController:
    """AnimationController 测试类"""

    def setup_method(self):
        """测试前设置"""
        self.test_dir = tempfile.mkdtemp()
        self.pet_dir = _create_pet_dir(
            self.test_dir, "cat",
            animations={
                "idle": {"fps": 12, "loop": True},
                "happy": {"fps": 12, "loop": True},
                "talk": {"fps": 12, "loop": False},
            })

    def teardown_method(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_init(self):
        """测试初始化"""
        ctrl = AnimationController(self.test_dir)
        assert ctrl.resource_path == self.test_dir
        assert ctrl.pet_name == ""
        assert ctrl.get_size() == (128, 128)
        assert ctrl.clips == {}
        assert ctrl.base_layer.name == "base"
        assert ctrl.expr_layer.name == "expression"
        assert ctrl.overlay_layer.name == "overlay"

    def test_load_pet_returns_true(self):
        """测试加载宠物成功返回True"""
        ctrl = AnimationController(self.test_dir)
        assert ctrl.load_pet("cat") is True
        assert ctrl.pet_name == "cat"
        assert "idle" in ctrl.clips
        assert "happy" in ctrl.clips
        assert "talk" in ctrl.clips

    def test_load_pet_missing_pet_returns_false(self):
        """测试不存在的宠物返回False"""
        ctrl = AnimationController(self.test_dir)
        assert ctrl.load_pet("nonexistent") is False

    def test_load_pet_no_manifest_returns_false(self):
        """测试没有manifest时返回False"""
        os.makedirs(os.path.join(self.test_dir, "naked"))
        ctrl = AnimationController(self.test_dir)
        assert ctrl.load_pet("naked") is False

    def test_load_pet_sets_size_from_manifest(self):
        """测试尺寸从manifest读取"""
        _create_pet_dir(self.test_dir, "pixel", size=[64, 96])
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("pixel")
        assert ctrl.get_size() == (64, 96)

    def test_load_pet_activates_idle_base_layer(self):
        """测试加载后idle被激活到基础层"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        assert ctrl.base_layer.is_active()
        assert ctrl.base_layer.clip is ctrl.clips["idle"]
        assert ctrl.expr_layer.is_active() is False
        assert ctrl.overlay_layer.is_active() is False

    def test_composite_blank_before_load(self):
        """测试加载前composite为透明图像"""
        ctrl = AnimationController(self.test_dir)
        img = ctrl.composite()
        assert img.width() == 128
        assert img.height() == 128
        assert (img.pixel(64, 64) >> 24) & 0xFF == 0

    def test_composite_size_from_manifest(self):
        """测试composite尺寸跟随manifest"""
        _create_pet_dir(self.test_dir, "pixel", size=[64, 96])
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("pixel")
        img = ctrl.composite()
        assert img.width() == 64
        assert img.height() == 96

    def test_composite_after_load_draws_base(self):
        """测试加载后composite绘制了基础帧"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img = ctrl.composite()
        assert (img.pixel(32, 32) >> 24) & 0xFF > 0

    def test_composite_returns_qimage(self):
        """测试composite返回QImage"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img = ctrl.composite()
        assert isinstance(img, QImage)

    def test_set_state_happy(self):
        """测试happy状态：idle基础层 + happy表情层"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.set_state("happy")
        assert ctrl.current_state == "happy"
        assert ctrl.base_layer.clip is ctrl.clips["idle"]
        assert ctrl.expr_layer.is_active()
        assert ctrl.expr_layer.clip is ctrl.clips["happy"]
        assert ctrl.overlay_layer.is_active() is False

    def test_set_state_switches_expression_and_clears_overlay(self):
        """测试状态切换时表情层切换、特效层清空"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.set_state("happy")
        assert ctrl.expr_layer.clip is ctrl.clips["happy"]
        ctrl.set_state("talk")
        assert ctrl.expr_layer.clip is ctrl.clips["talk"]
        assert ctrl.overlay_layer.is_active() is False

    def test_set_state_unknown_falls_back_to_idle(self):
        """测试未知状态回退到idle基础层"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.set_state("dance")
        assert ctrl.base_layer.clip is ctrl.clips["idle"]
        assert ctrl.expr_layer.is_active() is False
        assert ctrl.overlay_layer.is_active() is False

    def test_set_state_same_state_keeps_clip(self):
        """测试重复设置相同状态不重置（early return）"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.set_state("idle")
        assert ctrl.base_layer.clip is ctrl.clips["idle"]
        assert ctrl.base_layer.time == 0.0

    def test_update_advances_layers(self):
        """测试update推进图层时间"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.update(0.25)
        assert ctrl.base_layer.time == pytest.approx(0.25, abs=0.001)

    def test_update_inactive_layers_stay(self):
        """测试未激活图层update不推进"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.update(0.1)
        assert ctrl.expr_layer.time == 0.0
        assert ctrl.overlay_layer.time == 0.0

    def test_get_size_default(self):
        """测试默认尺寸"""
        ctrl = AnimationController()
        assert ctrl.get_size() == (128, 128)


class TestAnimationControllerCache:
    """脏帧缓存测试类"""

    def setup_method(self):
        """测试前设置"""
        self.test_dir = tempfile.mkdtemp()
        self.pet_dir = _create_pet_dir(
            self.test_dir, "cat",
            animations={
                "idle": {"fps": 12, "loop": True},
                "happy": {"fps": 12, "loop": True},
            })

    def teardown_method(self):
        """测试后清理"""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_composite_caches_same_key(self):
        """同一状态连续composite命中缓存并返回同一对象"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img1 = ctrl.composite()
        img2 = ctrl.composite()
        assert img1 is img2

    def test_composite_after_update_recomposites(self):
        """update推进时间后composite重新合成"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img1 = ctrl.composite()
        ctrl.update(1 / 30)
        img2 = ctrl.composite()
        assert img2 is not img1

    def test_composite_after_set_state_recomposites(self):
        """状态切换后composite重新合成"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img1 = ctrl.composite()
        ctrl.set_state("happy")
        img2 = ctrl.composite()
        assert img2 is not img1

    def test_update_invalidates_cache(self):
        """update后缓存失效（delta 必须跨帧边界：1/fps ≈ 0.083s @12fps）"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.composite()
        assert ctrl._cached_frame is not None
        ctrl.update(0.1)  # > 1/12 ≈ 0.083 → 跨帧，frame_changed=True
        assert ctrl._cached_frame is None
        assert ctrl._cache_key is None

    def test_set_state_invalidates_cache(self):
        """set_state后缓存失效"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.composite()
        ctrl.set_state("happy")
        assert ctrl._cached_frame is None
        assert ctrl._cache_key is None

    def test_set_state_same_state_keeps_cache(self):
        """重复设置相同状态不使缓存失效"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        ctrl.composite()
        ctrl.set_state("idle")
        assert ctrl._cached_frame is not None

    def test_cache_key_tracks_times_and_state(self):
        """缓存键包含三图层时间与当前状态"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        assert ctrl._cache_key is None
        ctrl.composite()
        assert ctrl._cache_key == (0.0, 0.0, 0.0, "idle")

    def test_cached_frame_is_valid_image(self):
        """缓存帧为有效QImage且尺寸正确"""
        ctrl = AnimationController(self.test_dir)
        ctrl.load_pet("cat")
        img = ctrl.composite()
        assert isinstance(img, QImage)
        assert not img.isNull()
        assert img.width() == 128
        assert img.height() == 128


class TestBuiltinAnimation:
    """内置后备动画测试类"""

    def test_frames_count_default(self):
        """测试默认生成帧（idle默认36帧）"""
        frames = generate_cat_idle_frames()
        assert len(frames) == 36

    def test_frames_count_custom(self):
        """测试自定义帧数"""
        frames = generate_cat_idle_frames(num_frames=6)
        assert len(frames) == 6

    def test_frames_size_default(self):
        """测试默认帧尺寸"""
        frames = generate_cat_idle_frames()
        for frame in frames:
            assert frame.width() == 128
            assert frame.height() == 128

    def test_frames_size_custom(self):
        """测试自定义帧尺寸"""
        frames = generate_cat_idle_frames(width=64, height=48, num_frames=4)
        for frame in frames:
            assert frame.width() == 64
            assert frame.height() == 48

    def test_frames_are_qimages(self):
        """测试返回值为QImage列表"""
        frames = generate_cat_idle_frames()
        for frame in frames:
            assert isinstance(frame, QImage)
            assert not frame.isNull()

    def test_frames_transparent_background(self):
        """测试四角透明背景"""
        frames = generate_cat_idle_frames()
        for frame in frames:
            for x, y in [(0, 0), (127, 0), (0, 127), (127, 127)]:
                assert (frame.pixel(x, y) >> 24) & 0xFF == 0, (
                    f"corner ({x},{y}) not transparent, pixel {frame.pixel(x, y):#010x}"
                )

    def test_frames_have_content(self):
        """测试身体区域有内容"""
        frames = generate_cat_idle_frames()
        for frame in frames:
            assert (frame.pixel(64, 74) >> 24) & 0xFF > 0

    def test_blink_frame_differs(self):
        """测试眨眼帧与普通帧在眼睛区域不同"""
        frames = generate_cat_idle_frames()
        diff_found = False
        for y in range(30, 50):
            for x in range(44, 70):
                if frames[5].pixel(x, y) != frames[0].pixel(x, y):
                    diff_found = True
                    break
            if diff_found:
                break
        assert diff_found, "Expected blink frame to differ in eye region"


class TestBuiltinAllStates:
    """generate_cat_frames 各状态生成测试类"""

    STATE_COUNTS = [("idle", 36), ("happy", 24), ("sleep", 36), ("talk", 20),
                    ("sad", 30), ("listen", 24), ("think", 36)]

    def test_generate_cat_frames_all_states(self):
        """测试各状态返回正确的帧数"""
        for state, expected in self.STATE_COUNTS:
            frames = generate_cat_frames(state)
            assert len(frames) == expected, (
                f"state '{state}' expected {expected} frames, got {len(frames)}")

    def test_all_state_frames_are_valid_qimages(self):
        """测试各状态帧均为非空QImage"""
        for state, expected in self.STATE_COUNTS:
            frames = generate_cat_frames(state)
            for frame in frames:
                assert isinstance(frame, QImage)
                assert not frame.isNull()

    def test_all_state_frames_default_size(self):
        """测试各状态帧默认尺寸128x128"""
        for state, _ in self.STATE_COUNTS:
            for frame in generate_cat_frames(state):
                assert frame.width() == 128
                assert frame.height() == 128

    def test_all_state_frames_custom_size(self):
        """测试各状态支持自定义尺寸"""
        for state, _ in self.STATE_COUNTS:
            frames = generate_cat_frames(state, 64, 48)
            assert len(frames) > 0
            for frame in frames:
                assert frame.width() == 64
                assert frame.height() == 48

    def test_unknown_state_falls_back_to_idle(self):
        """测试未知状态回退为idle（36帧）"""
        frames = generate_cat_frames("dance")
        assert len(frames) == 36  # idle默认36帧

    def test_talk_frames_alternate_mouth(self):
        """测试talk帧嘴部交替开合（偶数帧张嘴，奇数帧闭嘴）"""
        frames = generate_cat_frames("talk")
        mouth_region = [(x, y) for x in range(58, 71) for y in range(45, 58)]
        diff_frames = []
        for i in range(len(frames) - 1):
            differs = any(frames[i].pixel(x, y) != frames[i + 1].pixel(x, y)
                          for x, y in mouth_region)
            diff_frames.append(differs)
        assert all(diff_frames), "Expected every adjacent talk frame pair to differ in mouth region"

    def test_happy_frames_differ_from_idle_eyes(self):
        """测试happy眼睛(^^)与idle直视眼睛区域不同"""
        happy = generate_cat_frames("happy")
        idle = generate_cat_idle_frames()
        differs = any(happy[0].pixel(x, y) != idle[0].pixel(x, y)
                      for x in range(50, 79) for y in range(32, 46))
        assert differs, "Expected happy frame 0 to differ from idle frame 0 in eye region"

    def test_sleep_eyes_closed_vs_idle_open(self):
        """测试sleep闭眼与idle睁眼眼睛区域不同"""
        sleep = generate_cat_frames("sleep")
        idle = generate_cat_idle_frames()
        differs = any(sleep[0].pixel(x, y) != idle[0].pixel(x, y)
                      for x in range(50, 79) for y in range(30, 48))
        assert differs, "Expected sleep frame to differ from idle frame in eye region"


class TestControllerBuiltinFallback:
    """AnimationController 内置绘制兜底测试类"""

    def _write_manifest(self, pet_dir: Path, animations: dict) -> None:
        """写测试宠物的manifest（只写动画信息）"""
        manifest = {"name": "test", "size": [128, 128], "animations": animations}
        (pet_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_fallback_when_dir_empty(self, tmp_path):
        """测试空目录时生成帧并成功加载（brief Step 3 场景）"""
        pet_dir = tmp_path / "cat"
        (pet_dir / "idle").mkdir(parents=True)
        self._write_manifest(pet_dir, {"idle": {"frames": 36, "fps": 12, "loop": True}})
        ctrl = AnimationController(str(tmp_path))
        assert ctrl.load_pet("cat") is True
        assert "idle" in ctrl.clips
        img = ctrl.composite()
        assert not img.isNull()
        assert img.width() == 128
        assert img.height() == 128
        assert len(list((pet_dir / "idle").glob("*.png"))) == 36

    def test_fallback_composite_has_content(self, tmp_path):
        """测试兜底生成后合成画面非透明"""
        pet_dir = tmp_path / "cat"
        (pet_dir / "idle").mkdir(parents=True)
        self._write_manifest(pet_dir, {"idle": {"frames": 12, "fps": 12, "loop": True}})
        ctrl = AnimationController(str(tmp_path))
        ctrl.load_pet("cat")
        img = ctrl.composite()
        assert (img.pixel(64, 74) >> 24) & 0xFF > 0

    def test_fallback_respects_manifest_fps_and_loop(self, tmp_path):
        """测试兜底clip使用manifest的fps/loop"""
        pet_dir = tmp_path / "cat"
        (pet_dir / "idle").mkdir(parents=True)
        self._write_manifest(pet_dir, {"idle": {"frames": 12, "fps": 6, "loop": False}})
        ctrl = AnimationController(str(tmp_path))
        ctrl.load_pet("cat")
        clip = ctrl.clips["idle"]
        assert clip.fps == 6
        assert clip.loop is False

    def test_fallback_does_not_override_real_assets(self, tmp_path):
        """测试目录已有PNG时不生成（真实素材优先）"""
        pet_dir = tmp_path / "cat"
        anim_dir = pet_dir / "idle"
        anim_dir.mkdir(parents=True)
        real = QImage(128, 128, QImage.Format_ARGB32)
        real.fill(Qt.GlobalColor.red)
        assert real.save(str(anim_dir / "real_0001.png"))
        self._write_manifest(pet_dir, {"idle": {"frames": 12, "fps": 12, "loop": True}})
        ctrl = AnimationController(str(tmp_path))
        assert ctrl.load_pet("cat") is True
        assert len(list(anim_dir.glob("*.png"))) == 1, "Expected real PNG untouched"
        assert len(ctrl.clips["idle"].frames) == 1

    def test_fallback_all_manifest_states(self, tmp_path):
        """测试manifest全部状态均被兜底生成"""
        pet_dir = tmp_path / "cat"
        animations = {
            "idle": {"frames": 12, "fps": 12, "loop": True},
            "happy": {"frames": 12, "fps": 15, "loop": True},
            "sleep": {"frames": 10, "fps": 8, "loop": True},
            "talk": {"frames": 10, "fps": 12, "loop": False},
            "sad": {"frames": 10, "fps": 10, "loop": True},
            "listen": {"frames": 8, "fps": 10, "loop": True},
            "think": {"frames": 8, "fps": 10, "loop": True},
        }
        for anim_name in animations:
            (pet_dir / anim_name).mkdir(parents=True)
        self._write_manifest(pet_dir, animations)
        ctrl = AnimationController(str(tmp_path))
        assert ctrl.load_pet("cat") is True
        assert set(ctrl.clips.keys()) == set(animations.keys())
        expected_counts = {"idle": 36, "happy": 24, "sleep": 36, "talk": 20,
                           "sad": 30, "listen": 24, "think": 36}
        for anim_name, expected in expected_counts.items():
            generated = list((pet_dir / anim_name).glob("*.png"))
            assert len(generated) == expected, (
                f"state '{anim_name}' expected {expected} frames, got {len(generated)}")

    def test_fallback_mixed_real_and_generated(self, tmp_path):
        """测试混合场景：真实素材保留，空目录生成"""
        pet_dir = tmp_path / "cat"
        (pet_dir / "happy").mkdir(parents=True)
        (pet_dir / "sad").mkdir(parents=True)
        real = QImage(128, 128, QImage.Format_ARGB32)
        real.fill(Qt.GlobalColor.green)
        assert real.save(str(pet_dir / "happy" / "pic_01.png"))
        self._write_manifest(pet_dir, {
            "happy": {"frames": 12, "fps": 15, "loop": True},
            "sad": {"frames": 8, "fps": 10, "loop": True},
        })
        ctrl = AnimationController(str(tmp_path))
        assert ctrl.load_pet("cat") is True
        assert len(list((pet_dir / "happy").glob("*.png"))) == 1, "happy real asset kept"
        assert len(list((pet_dir / "sad").glob("*.png"))) == 30, "sad generated"
        ctrl.clips["happy"].load()
        ctrl.clips["sad"].load()
        assert ctrl.clips["happy"].frames[0].pixel(0, 0) == real.pixel(0, 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
