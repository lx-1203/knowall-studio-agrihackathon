import base from './playwright.model-configuration.config'
import { defineConfig } from '@playwright/test'
export default defineConfig({ ...base, testMatch: ['account-metrics-repair.spec.ts', 'api-configuration-repair.spec.ts', 'round37-settings-recovery.spec.ts', 'round38-settings-write-cleanup.spec.ts'] })
