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
