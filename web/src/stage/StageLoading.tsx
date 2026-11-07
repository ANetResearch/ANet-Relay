// StageLoading (05 §2.5): while the 3D stage mounts and the GLB loads, a 40 px VoxelLoader with "Loading aircraft
// model" sits in the middle of the stage; when the model is in, the voxels scatter outward and fade
// (--voxel-scatter). No scatter in reduced motion or on a software renderer (quality.light): it just goes.
import * as React from 'react'
import { useT } from '@/i18n'
import { msToken } from '@/ui/motion/tokens'
import { motionReduced } from '@/ui/motion/tier'
import { VoxelLoader } from '@/ui/voxel/VoxelLoader'
import { stageQuality } from './scene/gpu'
import { useStageModel } from './loading'

export function StageLoading() {
  const t = useT()
  const ready = useStageModel((s) => s.ready)
  // a later mount (the GLB is cached) shows nothing
  const [readyAtMount] = React.useState(ready)
  const [instant] = React.useState(() => motionReduced() || stageQuality().light)
  const [gone, setGone] = React.useState(false)
  React.useEffect(() => {
    if (!ready || instant || readyAtMount) return
    const id = setTimeout(() => setGone(true), msToken('--voxel-scatter', 350))
    return () => clearTimeout(id)
  }, [ready, instant, readyAtMount])
  if (readyAtMount || gone || (ready && instant)) return null
  return (
    <div className="stage-loading" data-passive="" data-out={ready ? '' : undefined} data-stage-loading="">
      <VoxelLoader size={40} label={t('stage.loadingModel')} caption={t('stage.loadingModel')} scatter={ready} />
    </div>
  )
}
