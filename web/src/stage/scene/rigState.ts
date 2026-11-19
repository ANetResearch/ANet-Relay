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

/**
 * three.js layer of the stage's own marks (rings, nose marker, base squares, altitude lines, trails, safety volume,
 * spawn and pad squares). The stage cameras see layers 0 and 1; the gimbal camera (12 §5.5) sees layer 0 only, so
 * its picture shows the world and not the stage's annotations.
 */
export const MARK_LAYER = 1

/** Puts an object and its descendants on the mark layer only. */
export function markLayer<T extends THREE.Object3D>(o: T): T {
  o.traverse((c) => c.layers.set(MARK_LAYER))
  return o
}
