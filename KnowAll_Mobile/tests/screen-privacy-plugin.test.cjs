const test = require('node:test')
const assert = require('node:assert/strict')
const { secureMainActivity } = require('../plugins/withScreenPrivacy.cjs')

for (const [language, source] of [
  ['kt', 'class MainActivity {\n  override fun onCreate(savedInstanceState: Bundle?) {\n    setTheme(R.style.AppTheme)\n    super.onCreate(null)\n  }\n}'],
  ['java', 'class MainActivity {\n  protected void onCreate(Bundle savedInstanceState) {\n    super.onCreate(savedInstanceState);\n  }\n}'],
]) {
  test(`${language}: capture protection is set before super.onCreate and remains idempotent`, () => {
    const secured = secureMainActivity(source, language)
    assert.match(secured, /windowManager|WindowManager/)
    assert.equal((secured.match(/FLAG_SECURE/g) || []).length, 1)
    assert.ok(secured.indexOf('FLAG_SECURE') < secured.indexOf('super.onCreate('))
    assert.equal(secureMainActivity(secured, language), secured)
    if (language === 'java') assert.match(secured, /FLAG_SECURE\);/)
  })
}

test('unsupported or changed activity templates stop the build instead of silently omitting protection', () => {
  assert.throws(() => secureMainActivity('class MainActivity {}', 'kt'), /onCreate/)
  assert.throws(() => secureMainActivity('super.onCreate(null)', 'swift'), /language/)
})
