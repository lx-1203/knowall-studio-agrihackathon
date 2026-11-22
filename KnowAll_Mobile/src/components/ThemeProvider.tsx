// Theme context for React Native (light/dark mode)
import React, { createContext, useContext, useState, useEffect, type ReactNode } from 'react'
import { useColorScheme } from 'react-native'
import { MD3LightTheme, MD3DarkTheme, Provider as PaperProvider } from 'react-native-paper'

interface ThemeContextType {
  isDark: boolean
  toggle: () => void
}

const ThemeContext = createContext<ThemeContextType>({ isDark: false, toggle: () => {} })

export function useAppTheme() {
  return useContext(ThemeContext)
}

const lightTheme = {
  ...MD3LightTheme,
  colors: {
    ...MD3LightTheme.colors,
    primary: '#32695B',
    onPrimary: '#FFFFFF',
    primaryContainer: '#E5EEE8',
    onPrimaryContainer: '#254C40',
    secondary: '#64716A',
    onSecondary: '#FFFFFF',
    secondaryContainer: '#EBEEE8',
    onSecondaryContainer: '#35443C',
    surfaceVariant: '#F0F0EA',
    onSurfaceVariant: '#6C726C',
    background: '#F8F8F4',
    surface: '#FFFFFC',
    onSurface: '#242A26',
    onBackground: '#242A26',
    outline: '#898F87',
    outlineVariant: '#E3E6DE',
    elevation: { level0: 'transparent', level1: '#F5F6F0', level2: '#F0F3EC', level3: '#EBF0E8', level4: '#E8EDE5', level5: '#E4EBE2' },
  },
}

const darkTheme = {
  ...MD3DarkTheme,
  colors: {
    ...MD3DarkTheme.colors,
    primary: '#A9CEBA',
    onPrimary: '#203F30',
    primaryContainer: '#2B4236',
    onPrimaryContainer: '#D4E7DA',
    secondary: '#B9C5BB',
    onSecondary: '#293B30',
    secondaryContainer: '#343E35',
    onSecondaryContainer: '#D9E3D9',
    surfaceVariant: '#262D27',
    onSurfaceVariant: '#ABB3AA',
    background: '#161B17',
    surface: '#1C221D',
    onSurface: '#E5E8DF',
    onBackground: '#E5E8DF',
    outline: '#7F897F',
    outlineVariant: '#364036',
    elevation: { level0: 'transparent', level1: '#232B24', level2: '#273128', level3: '#2B362C', level4: '#2D392E', level5: '#303D31' },
  },
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const systemScheme = useColorScheme()
  const [isDark, setIsDark] = useState(systemScheme === 'dark')

  useEffect(() => {
    setIsDark(systemScheme === 'dark')
  }, [systemScheme])

  const toggle = () => setIsDark(v => !v)

  const theme = isDark ? darkTheme : lightTheme

  return (
    <ThemeContext.Provider value={{ isDark, toggle }}>
      <PaperProvider theme={theme}>
        {children}
      </PaperProvider>
    </ThemeContext.Provider>
  )
}
