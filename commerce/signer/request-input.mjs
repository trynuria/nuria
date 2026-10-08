export async function readRequest(input = process.stdin, maximumBytes = 8192) {
  const chunks = [];
  let size = 0;
  for await (const chunk of input) {
    const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += bytes.length;
    if (size > maximumBytes) throw Error("Custody request exceeds byte limit");
    chunks.push(bytes);
  }
  const request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!request || typeof request !== "object" || Array.isArray(request))
    throw Error("Custody request must be an object");
  return request;
}
