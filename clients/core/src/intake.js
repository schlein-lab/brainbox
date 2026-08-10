
export async function sha256Hex(data) {
  const buf = data instanceof Blob ? await data.arrayBuffer() : data;
  const digest = await crypto.subtle.digest('SHA-256', buf);
  return [...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, '0')).join('');
}

export async function fileToManifest(file, { blobRef = null } = {}) {
  return { name: file.name || 'attachment', size: file.size,
    sha256: await sha256Hex(file), blob_ref: blobRef };
}

export const pathRef = (p) => p;

export function utteranceToVerb(utterance) {
  if (!utterance) return null;
  const u = utterance.trim().toLowerCase();
  if (["don't", 'do not', 'reject', 'deny', 'no, ', 'cancel', 'stop'].some(p => u.includes(p)))
    return { verb: 'deny' };
  if (u.startsWith('revise') || u.startsWith('change') || u.includes('revise')) {
    for (const lead of ['revise', 'change']) {
      if (u.startsWith(lead)) return { verb: 'steer', feedback: utterance.trim().slice(lead.length).replace(/^[\s:,-]+/, '') };
    }
    return { verb: 'steer', feedback: utterance.trim() };
  }
  if (['approve', 'yes', 'go ahead', 'confirm', 'do it', 'accept', 'okay', 'ok'].some(p => u.includes(p)))
    return { verb: 'approve' };
  return null;
}
