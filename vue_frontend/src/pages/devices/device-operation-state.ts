export function counterButtonState(
  tested: boolean,
  serialOpen: boolean,
  busy: boolean,
): { disabled: boolean; label: string } {
  return {
    disabled: !tested || !serialOpen || busy,
    label: busy ? '处理中…' : '清零',
  }
}

export function commandButtonState(
  serialOpen: boolean,
  busy: boolean,
): { disabled: boolean } {
  return { disabled: !serialOpen || busy }
}

export function batteryButtonState(
  serialOpen: boolean,
  busy: boolean,
): { disabled: boolean; label: string } {
  return {
    disabled: !serialOpen || busy,
    label: busy ? '正在逐台获取…' : '一键获取电池电压',
  }
}
