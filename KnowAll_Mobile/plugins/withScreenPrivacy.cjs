const { withMainActivity } = require('expo/config-plugins')

const marker = '// KnowAll: protect the window before React or restored screens can render.'

function secureMainActivity(contents, language) {
  if (language !== 'kt' && language !== 'java') {
    throw new Error(`Screen privacy: unsupported MainActivity language: ${language}`)
  }
  if (contents.includes(marker)) return contents
  const superCall = /^([ \t]*)super\.onCreate\s*\(/m
  if (!superCall.test(contents)) {
    throw new Error('Screen privacy: cannot find super.onCreate in MainActivity; refusing an unprotected build.')
  }
  const secureCall = language === 'kt'
    ? 'window.addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE)'
    : 'getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_SECURE);'
  return contents.replace(superCall, (_, indent) =>
    `${indent}${marker}\n${indent}${secureCall}\n${indent}super.onCreate(`)
}

module.exports = function withScreenPrivacy(config) {
  return withMainActivity(config, config => {
    config.modResults.contents = secureMainActivity(config.modResults.contents, config.modResults.language)
    return config
  })
}
module.exports.secureMainActivity = secureMainActivity
