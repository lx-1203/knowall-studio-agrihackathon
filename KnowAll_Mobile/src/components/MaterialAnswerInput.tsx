import { View, StyleSheet } from 'react-native'
import { Button, Icon, TouchableRipple, Text, TextInput, useTheme } from 'react-native-paper'
import type { MaterialQuestion } from '../materialLearning'
import { inputIssue, moveOrder, readBlanks, readMapping, readOrder, selectMapping, setBlank, toggleMultiple } from '../materialPractice'

export function MaterialChoice({ label, name, checked, multiple = false, disabled, onPress }: {
  label: string; name: string; checked: boolean; multiple?: boolean; disabled: boolean; onPress: () => void
}) {
  const theme = useTheme()
  return <TouchableRipple onPress={onPress} disabled={disabled} accessibilityLabel={name}
    accessibilityRole={multiple ? 'checkbox' : 'radio'} accessibilityState={{ checked, disabled }}
    aria-checked={checked} aria-disabled={disabled}>
    <View style={styles.choice}>
      <Icon source={multiple ? checked ? 'checkbox-marked' : 'checkbox-blank-outline' : checked ? 'radiobox-marked' : 'radiobox-blank'} size={24}
        color={disabled ? theme.colors.onSurfaceDisabled : checked ? theme.colors.primary : theme.colors.onSurfaceVariant} />
      <Text style={[styles.label, { color: disabled ? theme.colors.onSurfaceDisabled : theme.colors.onSurface }]}>{label}</Text>
    </View>
  </TouchableRipple>
}

export default function MaterialAnswerInput({ question, value, onChange, disabled }: {
  question: MaterialQuestion; value: string; onChange: (value: string) => void; disabled: boolean
}) {
  const theme = useTheme()
  const issue = inputIssue(question)
  if (issue) return <Text style={{ color: theme.colors.error }}>{issue}</Text>
  const options = question.options || []
  if (['matching', 'classification'].includes(question.question_type)) {
    const mapping = readMapping(value)
    return <View style={styles.stack}>
      <Text>已选择 {options.filter(option => option.choices?.includes(mapping[option.label])).length} / {options.length} 项 · 每项选择一个答案</Text>
      {options.map(option => <View key={option.label} style={[styles.group, { borderColor: theme.colors.outlineVariant }]}>
        <Text variant="titleSmall">{option.label}. {option.text}</Text>
        {option.choices!.map(choice => <MaterialChoice key={choice} label={choice} checked={mapping[option.label] === choice} disabled={disabled}
          name={`${option.label} 的答案：${choice}`} onPress={() => onChange(selectMapping(question, value, option.label, choice))} />)}
      </View>)}
    </View>
  }
  if (question.question_type === 'ordering') return <View style={styles.stack}>
    <Text>调整顺序后提交；也可以保留当前顺序。</Text>
    {readOrder(question, value).map((label, index, order) => <View key={label} style={[styles.group, { borderColor: theme.colors.outlineVariant }]}>
      <Text>{index + 1}. {options.find(option => option.label === label)?.text}</Text>
      <View style={styles.actions}><Button compact accessibilityLabel={`${label} 上移`} disabled={disabled || index === 0} onPress={() => onChange(moveOrder(question, value, index, -1))}>上移</Button>
        <Button compact accessibilityLabel={`${label} 下移`} disabled={disabled || index === order.length - 1} onPress={() => onChange(moveOrder(question, value, index, 1))}>下移</Button></View>
    </View>)}
  </View>
  if (['multi_choice', 'multiple_choice'].includes(question.question_type)) return <View>
    <Text>可选择多个答案</Text>
    {options.map(option => <MaterialChoice key={option.label} label={`${option.label}. ${option.text}`} name={`选择 ${option.label}：${option.text}`} checked={value.split(',').includes(option.label)} multiple onPress={() => onChange(toggleMultiple(question, value, option.label))} disabled={disabled} />)}
  </View>
  if (['single_choice', 'true_false'].includes(question.question_type)) {
    const choices = question.question_type === 'true_false' ? [{ label: 'true', text: '正确' }, { label: 'false', text: '错误' }] : options
    return <View>{choices.map(option => <MaterialChoice key={option.label} label={question.question_type === 'true_false' ? option.text : `${option.label}. ${option.text}`} name={`选择 ${option.label}：${option.text}`} checked={value === option.label} disabled={disabled} onPress={() => onChange(option.label)} />)}</View>
  }
  if (question.question_type === 'fill_blank' && question.blank_count) return <View style={styles.stack}>
    {readBlanks(value, question.blank_count).map((text, index) => <TextInput key={index} mode="outlined" label={`第${index + 1}空`} accessibilityLabel={`第${index + 1}空`} value={text} multiline disabled={disabled} onChangeText={text => onChange(setBlank(value, question.blank_count!, index, text))} />)}
  </View>
  return <TextInput mode="outlined" label="你的答案" accessibilityLabel="你的答案" multiline value={value} onChangeText={onChange} disabled={disabled} />
}
const styles = StyleSheet.create({ stack: { gap: 12 }, group: { padding: 12, borderWidth: 1, borderRadius: 12, gap: 8 }, actions: { flexDirection: 'row', flexWrap: 'wrap' }, choice: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10 }, label: { textAlign: 'left', flexShrink: 1 } })
