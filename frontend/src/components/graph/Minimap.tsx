import type { RefObject } from 'react'

interface MinimapProps {
  minimapRef: RefObject<HTMLCanvasElement>
}

/** 图谱缩略图容器（绘制逻辑由页面的 drawMinimap 持有） */
export default function Minimap({ minimapRef }: MinimapProps) {
  return (
    <div className="graph-minimap">
      <canvas ref={minimapRef} width={140} height={90} />
    </div>
  )
}