import { Card, Image, Space, Typography } from 'antd';
import type { ResultDetail } from '../api/client';

export function MediaImages({ images }: { images?: ResultDetail['images'] }) {
  if (!images?.length) return null;
  return <Card size="small" title={`生成图片（${images.length} 张）`} style={{ marginBottom: 16 }}>
    <Image.PreviewGroup><Space wrap align="start">
      {images.map((item) => <div key={item.file}>
        <Image width={220} src={item.local_url} alt={item.revised_prompt || item.file} />
        <div><Typography.Link href={item.local_url} download={item.file}>下载 {item.file}</Typography.Link></div>
      </div>)}
    </Space></Image.PreviewGroup>
  </Card>;
}
