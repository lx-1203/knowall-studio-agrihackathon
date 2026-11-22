import { Skeleton, Card, Space } from 'antd'

export function PageSkeleton() {
  return (
    <div style={{ padding: 24 }}>
      <Skeleton active paragraph={{ rows: 1 }} />
      <Space direction="vertical" style={{ width: '100%', marginTop: 16 }}>
        {[1, 2, 3].map(i => (
          <Card key={i}><Skeleton active paragraph={{ rows: 3 }} /></Card>
        ))}
      </Space>
    </div>
  )
}

// CardSkeleton / MindMapSkeleton / QuizSkeleton were removed: an export audit
// found zero references to any of them outside this file (ChatSkeleton and
// PageSkeleton are the two that are actually used).

export function ChatSkeleton() {
  return (
    <div style={{ padding: 16 }}>
      {[1, 2, 3].map(i => (
        <div key={i} style={{ marginBottom: 24, display: 'flex', gap: 12, flexDirection: i % 2 ? 'row' : 'row-reverse' }}>
          <Skeleton.Avatar active size={36} />
          <Skeleton active paragraph={{ rows: 2 }} style={{ width: '60%' }} />
        </div>
      ))}
    </div>
  )
}
