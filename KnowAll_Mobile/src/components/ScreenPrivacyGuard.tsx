import React, { type ReactNode } from 'react'

/** Web browsers do not offer the native app's system capture protection. */
export default function ScreenPrivacyGuard({ children }: { children: ReactNode }) {
  return <>{children}</>
}
