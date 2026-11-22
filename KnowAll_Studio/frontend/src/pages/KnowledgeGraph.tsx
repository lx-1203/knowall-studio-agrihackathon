import { useEffect } from 'react'
import type { Dispatch, MutableRefObject, SetStateAction } from 'react'
import { Button, Card, Space, Tag } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import ReactFlow, {
  Background, Controls, MiniMap, Panel, ReactFlowProvider,
  applyNodeChanges, applyEdgeChanges, useReactFlow,
} from 'reactflow'
import type { Connection, Edge, Node } from 'reactflow'
import KnowledgeNode from '../components/KnowledgeNode'

const nodeTypes = { knowledgeNode: KnowledgeNode }

interface KnowledgeGraphProps {
  nodes: Node[]
  edges: Edge[]
  setNodes: Dispatch<SetStateAction<Node[]>>
  setEdges: Dispatch<SetStateAction<Edge[]>>
  onConnect: (connection: Connection) => void
  edgeMode: boolean
  knowledgeEdges: any[]
  onDeleteEdge: (edgeId: string) => void
  fitViewRef: MutableRefObject<() => void>
}

function GraphCanvas({ nodes, edges, setNodes, setEdges, onConnect, edgeMode, knowledgeEdges, onDeleteEdge, fitViewRef }: KnowledgeGraphProps) {
  const { fitView } = useReactFlow()

  useEffect(() => {
    fitViewRef.current = () => { fitView({ padding: 0.2, duration: 300 }) }
    return () => { fitViewRef.current = () => {} }
  }, [fitView, fitViewRef])

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={changes => setNodes(current => applyNodeChanges(changes, current))}
      onEdgesChange={changes => setEdges(current => applyEdgeChanges(changes, current))}
      onConnect={onConnect}
      nodeTypes={nodeTypes}
      fitView
      attributionPosition="bottom-left"
    >
      <Background color="#f0f0f0" gap={20} />
      <Controls />
      <MiniMap nodeColor="#4f46e5" maskColor="rgba(0,0,0,0.08)" />
      <Panel position="top-right">
        <Space direction="vertical" size={4}>
          <Tag color="blue" style={{ fontSize: 12 }}>拖拽节点编辑 · 滚轮缩放</Tag>
          {edgeMode && <Tag color="orange" style={{ fontSize: 12 }}>连线模式: 拖拽节点端口创建连线</Tag>}
        </Space>
      </Panel>
      {knowledgeEdges.length > 0 && (
        <Panel position="bottom-left">
          <Card size="small" title="跨引用连线" style={{ maxHeight: 200, overflow: 'auto', width: 260 }}>
            {knowledgeEdges.map((e: any) => {
              const relLabels: Record<string, string> = { related_to: '相关', prerequisite: '前置', extends: '扩展', contradicts: '矛盾', example_of: '举例' }
              return (
                <div key={e.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4, fontSize: 12 }}>
                  <Tag>{relLabels[e.relation_type] || e.relation_type}</Tag>
                  <span style={{ color: '#999' }}>{e.source_node_id?.slice(0, 6)}→{e.target_node_id?.slice(0, 6)}</span>
                  <Button size="small" type="link" danger icon={<DeleteOutlined />} onClick={() => onDeleteEdge(e.id)} />
                </div>
              )
            })}
          </Card>
        </Panel>
      )}
    </ReactFlow>
  )
}

export default function KnowledgeGraph(props: KnowledgeGraphProps) {
  return <ReactFlowProvider><GraphCanvas {...props} /></ReactFlowProvider>
}
