# 3D 场景资源

当前使用哪个 3D 建筑、各个 GLB 精度版本以及它在模拟坐标里的摆放方式，统一由
`scene-config.json` 控制。主机端和手机端都会读取 `/api/scene-models`，因此换建筑不需要改网页代码。

当前本地场景文件：

- `uncle_mugens_school_building_meshopt.glb`：约 83 MB；使用 `EXT_meshopt_compression`，几何精度高，需浏览器允许本地 WASM 解码。
- `uncle_mugens_school_building_opt.glb`：当前推荐版本，约 102 MB；抽稀版本，不需要 meshopt 解码器，渲染负载更轻，局部可能出现破面。
- `uncle_mugens_school_building.glb`：原始版本，约 476 MB；精度最高，适合桌面端排查。
- `CREDITS.txt`：作者署名和许可证。

这些 `.glb` 文件被 `.gitignore` 排除，因为普通 Git 仓库不适合保存这么大的二进制文件。若要交给其他电脑使用，请使用 Git LFS 或对象存储，并保持文件放在：

```text
main/static/scenes/
```

网页端优先选择配置中标记为 `preferred` 的模型；不可用时会跳过不可解码版本，或尝试 `fallback` 兼容版本。手机端也可以通过 URL 强制选择当前场景中的模型文件：

```text
/play?scene=school&model=meshopt    # meshopt 无损压缩版
/play?scene=school&model=opt   # 抽稀兼容版
/play?scene=school&model=full  # 原始版
```

## 更换 / 新增场景

1. 把新的 `.glb` 文件放到 `main/static/scenes/`。
2. 编辑 `scene-config.json`：将 `active` 指向要展示的场景 `id`，并在该场景的 `models` 中填写文件名。把最适合当前电脑的模型设为 `preferred: true`，兼容版可标记 `fallback: true`；Meshopt 压缩模型设 `meshopt: true`。
3. 调整 `placement`：`fit` 控制模拟区域相对模型的占比，`rotationY` 是模型绕 Y 轴旋转弧度，`centerX` / `centerZ` 是模拟区域中心在场景中的相对位置（0–1）。`simulationArea` 用模型地面包围盒的归一化比例指定实际疏散区域，可裁掉院子、停车场等非模拟部分；默认 `{ "minX": 0, "minZ": 0, "maxX": 1, "maxZ": 1 }` 使用整个场景范围。手机和主机 3D 使用同一映射。
4. 刷新主机页和手机页；服务端会按清单列出实际存在的模型文件。

每个场景示例：

```json
{
  "id": "office",
  "name": "办公楼",
  "models": [
    { "file": "office-light.glb", "preferred": true },
    { "file": "office-full.glb", "fallback": true }
  ],
  "placement": {
    "fit": 0.92,
    "rotationY": 0,
    "centerX": 0.5,
    "centerZ": 0.5,
    "simulationArea": { "minX": 0.1, "minZ": 0.1, "maxX": 0.9, "maxZ": 0.9 }
  }
}
```

当前真实建筑模型用于视觉观察与角色坐标映射；碰撞、出口和 NPC 寻路仍由网格模拟层负责。主机 3D 默认隐藏网格，工具栏的「显示网格」可用于对照校准。

## 自动校准结果与当前学校场景

校准页的“自动校准”会先分析模型楼层和水平地面，把候选显示在浏览器草稿中，由管理员确认后再替换可走范围。替换流程会保留并重新关联起始区、门、楼梯和出口；楼层、区域或标注无法唯一关联时，整次应用失败，草稿和正式场景都不改变。正式保存前，服务端仍会执行场景结构、楼梯跨层和出口可达性校验。

当前 `scene-v2.json` 的学校场景使用自动校准得到的完整楼面轮廓：

- 四层楼面高度为 0.12 / 1.32 / 2.52 / 3.72 m。
- 每层保留西教室、东教室和主区域，共 12 个可走区。
- 12 个起始区、16 扇门、3 段楼梯和 1 个出口保持原 ID；门的两侧区域、起始区、楼梯端点和出口所属区域已重新解析。
- 尺度仍为 `provisional`，需要根据现场已知长度或可信 BIM 单位复核后才能用于正式实验。

自动分析识别出的 5 个大型障碍保留为与外轮廓相连的不可通行窄缝，828 个桌椅、装饰面等碎片孔洞被过滤。这样既保持障碍不可通行，又避免大量碎片孔洞使现有导航三角化变慢。

场景还带有 `annotation_recheck: needs-recheck` 和 `navigation_review: needs-review`，表示几何已自动重建但消防语义和现场通行关系仍需人工复核。

## 前端依赖

自动替换模块使用本地 ESM 版 `vendor/polygon-clipping.esm.min.js`，不依赖 CDN。对应的 `POLYGON-CLIPPING-LICENSE.txt`、`ROBUST-PREDICATES-LICENSE.txt` 和 `SPLAYTREE-LICENSE.txt` 随依赖一起保存。生成的 `.gz` 静态压缩文件由部署流程生成，不提交到 Git。
