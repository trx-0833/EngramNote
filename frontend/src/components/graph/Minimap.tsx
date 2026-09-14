import type { RefObject } from 'react'
// 图谱功能的类名归模块所有（overhaul-plan 5.6 序 10）：见 Graph.module.css 文件头
import styles from './Graph.module.css'

interface MinimapProps {
  minimapRef: RefObject<HTMLCanvasElement>
}

/** 图谱缩略图容器（绘制逻辑由页面的 drawMinimap 持有） */
export default function Minimap({ minimapRef }: MinimapProps) {
  return (
    <div className={styles.graphMinimap}>
      <canvas ref={minimapRef} width={140} height={90} />
    </div>
  )
}