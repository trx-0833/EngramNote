/**
 * @file 视频笔记播放器
 * @description
 * 由 `pages/NoteDetail.tsx` 拆分而来（overhaul-plan 5.5），只做搬运：
 * blob URL 的获取与释放仍留在页面（`NoteDetail.tsx`）里，
 * 本组件只负责渲染播放器。
 */
interface VideoPlayerProps {
  /** 携带 JWT 拉取后生成的 blob URL */
  videoUrl: string;
}

/** 视频播放器（仅视频类型笔记显示） */
export default function VideoPlayer({ videoUrl }: VideoPlayerProps) {
  return (
    <div style={{ marginBottom: '1.5rem' }}>
      <video controls style={{ width: '100%', borderRadius: '0.5rem' }} src={videoUrl}>
        您的浏览器不支持视频播放
      </video>
    </div>
  );
}
