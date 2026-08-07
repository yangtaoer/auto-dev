# AutoDev 品牌图像处理记录

## 权威源图

`autodev-logo-source.png` 是用户确认的最终 Logo，原文件已包含透明通道。生产资源由 `scripts/build-brand-assets.py` 直接从该源图裁切和缩放，确保图形、渐变、字形与大小写不发生变化。

## ImageGen 透明背景验证提示词

```text
Use case: background-extraction
Asset type: master brand logo for web, desktop client, email, favicon derivation
Primary request: Reproduce the supplied AutoDev logo exactly, changing only its background to a perfectly flat solid #00ff00 chroma-key background for later removal.
Input images: Image 1 is the authoritative edit target and must be preserved exactly.
Subject: the purple-to-blue infinity loop formed from code angle brackets, with the exact wordmark "AutoDev" below it.
Style/medium: crisp polished digital logo, preserve the existing bevels, gradients, proportions, letterforms, and spacing.
Composition/framing: centered, generous even padding, no cropping.
Text (verbatim): "AutoDev"
Constraints: change only the background; keep the logo symbol and exact AutoDev wordmark unchanged; background must be one uniform #00ff00 with no shadows, gradients, texture, reflections, floor plane, or lighting variation; no #00ff00 inside the logo; no cast shadow; no watermark; no extra text.
Avoid: redesigning the mark, changing colors, changing typography, changing capitalization, adding any tagline or decorative element.
```

ImageGen 输出只用于验证透明化方案。检查后发现权威源图已经具有干净的透明通道，因此没有采用会造成字形或渐变漂移的重绘结果。
