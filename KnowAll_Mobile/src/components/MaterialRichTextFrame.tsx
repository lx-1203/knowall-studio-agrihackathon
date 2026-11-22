import { useEffect, useRef, useState } from 'react'
import { WebView } from 'react-native-webview'

const measure = `(()=>{const send=()=>window.ReactNativeWebView.postMessage(JSON.stringify({height:Math.ceil(document.body.getBoundingClientRect().height)+16}));send();if(window.ResizeObserver)new ResizeObserver(send).observe(document.body);document.fonts?.ready.then(send);})();true;`
export default function MaterialRichTextFrame({ html, onFailure }: { html: string; onFailure: () => void }) {
  const [height, setHeight] = useState(120)
  const measuredOnce = useRef(false)
  useEffect(() => {
    const timer = setTimeout(() => { if (!measuredOnce.current) onFailure() }, 5000)
    return () => clearTimeout(timer)
  }, [onFailure])
  return <WebView source={{ html, baseUrl: 'about:blank' }} style={{ height, backgroundColor: 'transparent' }} containerStyle={{ flex: 0 }}
    originWhitelist={['*']} onShouldStartLoadWithRequest={request => request.url === 'about:blank'}
    scrollEnabled={false} bounces={false} showsVerticalScrollIndicator={false} javaScriptEnabled
    injectedJavaScript={measure} onError={onFailure} onHttpError={onFailure} onContentProcessDidTerminate={onFailure}
    allowFileAccess={false} allowUniversalAccessFromFileURLs={false} allowFileAccessFromFileURLs={false}
    setSupportMultipleWindows={false} mixedContentMode="never" domStorageEnabled={false}
    onMessage={event => {
      try {
        const measured = JSON.parse(event.nativeEvent.data).height
        if (!Number.isFinite(measured) || measured <= 0 || measured > 60000) { onFailure(); return }
        measuredOnce.current = true
        setHeight(Math.ceil(measured))
      } catch { onFailure() }
    }} />
}
