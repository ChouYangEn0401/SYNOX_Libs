// 轉接器：畫面只經 SynoxApp（read／act），不自己連線。錯誤統一成一句給人看的話。
function kit() {
  const k = globalThis.SynoxApp;
  if (!k) throw new Error("沒有從 SYNOX 打開這個畫面，回到工具頁再按一次「打開」");
  return k;
}

export const read = (name, params) => kit().read(name, params || {});

// 寫入（危險的動作 SYNOX 會先跳確認框）。成功回資料；失敗丟 Error（message 是給人看的話）。
export async function act(name, values) {
  try {
    const out = await kit().act(name, values || {}, { quiet: true });
    if (out && out.fields && Object.keys(out.fields).length) throw new Error(Object.values(out.fields).join("；"));
    return out || {};
  } catch (e) {
    throw new Error((e && (e.error || e.message)) || "發生錯誤");
  }
}
