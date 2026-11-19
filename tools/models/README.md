# tools/models

Generators for the 3D stage's aircraft models (`web/public/models/`).

## ANetDU7 (YiX Tangyuan U7, 汤圆 U7)

```sh
node tools/models/build_anet_du7.mjs              # writes web/public/models/anet_du7.glb
node tools/models/check_anet_du7.mjs              # structural checks (names, pivots, size, budget)
node tools/models/preview/shoot.mjs --out /tmp/du7 # screenshots with the stage's light rig
```

`build_anet_du7.mjs` is a procedural, deterministic generator (Node 22, ESM). It uses three.js from
`web/node_modules` for geometry only and writes the GLB container itself: core glTF 2.0, factor-only PBR materials, no
textures, images, extensions, cameras, lights or animations. Dimensions come from the airframe CAD export
`vehicles/anet-du7/onboard/new_u7_src/yydrone_description/meshes/yix.stl` (measured, not copied) and the product
sheet; colours and the white printed parts follow the product photo and poster. The header comment of the generator
lists the sources and conventions in full.

Conventions the stage relies on:

- metres, real size, +Y up, nose along +Z; origin at the centre in X/Z, y = 0 under the landing pads;
  overall 0.684 x 0.211 x 0.684 m (X, Y, Z);
- the pilot's left is +X (nose +Z cross up +Y gives right = -X);
- `Propeller_FL/FR/RL/RR_ROTATABLE`: one mesh each, origin on the spin axis at the hub, spin axis local +Y,
  translation-only nodes at (+-0.1768, 0.1325, +-0.1768); FL and RR turn clockwise seen from above, FR and RL
  counter-clockwise, and the blade geometry is handed to match;
- `Gimbal_Yaw` > `Gimbal_Pitch` > `Gimbal_Camera`: translation-only pivots (yaw about local Y, pitch about local X,
  camera empty at the zoom lens looking along local +Z); the yaw-stage and head meshes are their children
  (`GimbalYawStage__*`, `GimbalHead__*`). Right-hand angles: +yaw turns the camera towards +X, +pitch looks down;
- every other node is static and named `<Part>__<material>`, so the stage can merge them per material.

`preview/` holds the screenshot harness: `preview.html` reproduces the stage lighting (hemisphere g200/g950 0.6, key
light 1.4, ACES at exposure 1.35, shadow catcher) and `shoot.mjs` drives it in headless Chromium through the repo's
Playwright with software WebGL. Presets: `hero front side rear top under gimbal nose posed posedtop low photo far
corner deck lidar belly`; `--opts '{"merged":true}'` renders the file merged the way `web/src/stage/scene/model.ts`
does, `--model` also accepts other GLBs or a ROS `.stl`.
