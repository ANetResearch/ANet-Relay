// Mutable camera rig state shared by scene parts that follow the view (grid fade centre, shadow area). Written by
// CameraRig every frame; read by others in their own frame callbacks. Scene coordinates.
import * as THREE from 'three'

export const rig = {
  /** what the camera looks at */
  target: new THREE.Vector3(),
  /** half extent (m) of the area the camera frames, for shadow and contact-shadow coverage */
  extent: 2,
  /** the camera rendering right now (read by labels and probes) */
  camera: null as THREE.Camera | null,
}
