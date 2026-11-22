import { Link, useLocation } from 'react-router-dom'
import { libraryReturn } from '../materialProgress'
import { ArrowLeftOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import '../materials.css'

export default function BuiltinMaterialPage() {
  const location = useLocation()
  return <section className="material-study">
    <header className="material-study-header"><div><Link className="material-back" to={libraryReturn(location.state)}><ArrowLeftOutlined /> 我的资料</Link><h2>分子生物学 · 章节习题</h2><Tag>生物</Tag><Tag>内置题库</Tag></div><p className="material-study-hint">按章节练习，沿用原有作答记录。</p></header>
    <iframe className="material-builtin-frame" src="/molbio-quiz.html" title="分子生物学刷题" />
  </section>
}
