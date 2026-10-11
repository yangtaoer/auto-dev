# UI 图片与视频 OSS 加速

公开界面素材通过 `app/static/media-manifest.json` 映射到 OSS HTTPS 地址，直接由浏览器获取，后端和执行器不转发大文件。云端不需要 OSS 密钥。逻辑文件路径和本地原件保留，关闭 `AUTODEV_UI_MEDIA_ENABLED` 或加载失败时可回退；主题设置、动效和交付文件不受影响。

## 更新素材

1. 修改 `app/static/brand/`、`media/`、`themes/backgrounds/` 或 `themes/previews/` 的公开素材。
2. 在本机设置 `AUTODEV_ENV_FILE` 指向 `local-runner/.env.runner`，使用执行器的 Python 运行 `scripts/publish-ui-media.py`；可先加 `--dry-run` 估算。发布器需要 OSS SDK 和 `scripts/requirements-media.txt` 的 Pillow。
3. 发布器校验每个对象匿名 HEAD、缓存、大小、类型，以及视频的 206 Range 分段读取。全部成功后才生成新的映射。
4. 按常规流程测试、提交映射和发布代码。不要提交密钥、临时转换文件或环境文件。

主题图转换为 WebP，背景保留足够清晰度，预览图按卡片尺寸缩小。文件名带内容哈希，一年 immutable 缓存，不随应用版本重复下载；内容变更使用新地址。图片异步解码，预览卡片仅在打开主题面板时懒加载。视频保持原比例、自动播放、海报与原布局。

UI 对象使用 `autodev-static/v1/` 独立前缀并设置对象级 public-read；不会修改桶 ACL、CORS、生命周期或交付物权限。执行器交付物仍在 `autodev/`，三天自动清理不会删除 UI 素材。以后若配置桶级生命周期，也需要排除静态目录；旧的哈希对象可留作版本回退，不自动删除。

OSS 加速的是资源传输，不能消除所有 3D 渲染开销。可在个人外观中使用轻柔或静止动效降低较弱设备的 GPU 负担。此方案未开通付费 CDN、跨区域复制或额外加速服务；未来可再按访问地区配置 CDN 域名。
