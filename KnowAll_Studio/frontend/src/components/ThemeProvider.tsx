import { createContext, useContext, useEffect, useState, ReactNode } from 'react'
import { ConfigProvider, theme as antTheme } from 'antd'

interface ThemeContextType {
  isDark: boolean
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextType>({ isDark: false, toggle: () => {} })

export function useTheme() {
  return useContext(ThemeContext)
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [isDark, setIsDark] = useState(() => {
    const stored = localStorage.getItem('knowall-theme')
    return stored === 'dark'
  })

  useEffect(() => {
    const appearance = isDark ? 'dark' : 'light'
    localStorage.setItem('knowall-theme', appearance)
    document.documentElement.setAttribute('data-theme', appearance)
  }, [isDark])

  const toggle = () => setIsDark(v => !v)

  return (
    <ThemeContext.Provider value={{ isDark, toggle }}>
      <ConfigProvider theme={{
        algorithm: isDark ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: {
          colorPrimary: isDark ? '#4bbdb0' : '#147d73', borderRadius: 8,
          colorBgLayout: isDark ? '#0e1d2a' : '#f4f7f9',
          colorBgContainer: isDark ? '#142636' : '#ffffff',
          colorText: isDark ? '#e4edf4' : '#183449',
          colorTextSecondary: isDark ? '#a7bccd' : '#587084',
          colorTextPlaceholder: isDark ? '#90a9bd' : '#657b8d',
          colorBorder: isDark ? '#385065' : '#cad8e2',
          fontFamily: "system-ui, 'Microsoft YaHei', sans-serif",
        },
      }}>
        {children}
      </ConfigProvider>
    </ThemeContext.Provider>
  )
}
