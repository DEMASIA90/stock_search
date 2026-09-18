const decoder = new TextDecoder();
const encoder = new TextEncoder();

function fromBase64(value) {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

export async function decryptEnvelope(envelope, password) {
  if (envelope?.empty) {
    const error = new Error(envelope.message || "아직 게시된 자산 데이터가 없습니다.");
    error.code = "EMPTY";
    throw error;
  }
  if (envelope?.version !== 1 || envelope?.kdf?.name !== "PBKDF2" || envelope?.cipher?.name !== "AES-GCM") {
    throw new Error("지원하지 않는 데이터 형식입니다.");
  }
  if (Number(envelope.kdf.iterations) < 100000) {
    throw new Error("안전하지 않은 암호화 설정입니다.");
  }

  const passwordKey = await crypto.subtle.importKey(
    "raw", encoder.encode(password), "PBKDF2", false, ["deriveKey"]
  );
  const key = await crypto.subtle.deriveKey(
    {
      name: "PBKDF2",
      salt: fromBase64(envelope.kdf.salt),
      iterations: Number(envelope.kdf.iterations),
      hash: "SHA-256",
    },
    passwordKey,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"],
  );
  const plaintext = await crypto.subtle.decrypt(
    {
      name: "AES-GCM",
      iv: fromBase64(envelope.cipher.iv),
      additionalData: encoder.encode(envelope.cipher.aad || "PersonalAssetWeb:v1"),
      tagLength: 128,
    },
    key,
    fromBase64(envelope.data),
  );
  return JSON.parse(decoder.decode(plaintext));
}
