// WP-A (05 §2.5): whether the stage has its aircraft model. The scene marks it once the GLB (or the fallback
// airframe, or the no-WebGL notice) is in place; StageLoading shows the voxel loader until then. The GLB is cached
// for the window's lifetime, so a later stage mount starts ready.
import { create } from 'zustand'

export const useStageModel = create<{ ready: boolean }>()(() => ({ ready: false }))

export function markStageModelReady(): void {
  if (!useStageModel.getState().ready) useStageModel.setState({ ready: true })
}
