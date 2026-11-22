import test from 'node:test'
import assert from 'node:assert/strict'
import { screenError } from '../src/userFacingError.ts'

test('an unavailable API uses the screen recovery message instead of Not Found', () => {
  const message = '暂时无法完成检索，你输入的关键词已保留，请稍后再试。'
  assert.equal(screenError({ response: { status: 404, data: { detail: 'Not Found' } } }, message), message)
})

test('server details and validation internals never become user-facing copy', () => {
  for (const detail of ['Internal Server Error', '数据库异常：SELECT secret FROM users', '<html>nginx error</html>', [{ loc: ['body', 'query'], msg: 'Field required' }]]) {
    for (const status of [400, 422, 500, 502, 503]) {
      assert.equal(screenError({ response: { status, data: { detail } } }, '请稍后再试。'), '请稍后再试。')
    }
  }
})

test('unknown outcomes retain the page-specific guidance about saved input', () => {
  const message = '保存结果暂未确认，输入已保留，请先核对后再操作。'
  for (const error of [undefined, null, 'offline', new Error('Network Error'), { response: { status: 504 } }]) {
    assert.equal(screenError(error, message), message)
  }
})

test('session, access and rate limits offer clear next steps without raw details', () => {
  assert.match(screenError({ response: { status: 401 } }, '失败'), /重新登录/)
  assert.match(screenError({ response: { status: 403 } }, '失败'), /权限/)
  assert.match(screenError({ response: { status: 429 } }, '失败'), /稍等/)
})
