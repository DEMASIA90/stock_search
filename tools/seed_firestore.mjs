// Run with Application Default Credentials or GOOGLE_APPLICATION_CREDENTIALS.
// Examples: gcloud auth application-default login / service account JSON path.
import { initializeApp, applicationDefault } from 'firebase-admin/app';
import { getFirestore } from 'firebase-admin/firestore';
import { readFileSync } from 'node:fs';
const json=JSON.parse(readFileSync(new URL('../data/questions.json',import.meta.url),'utf8'));
const projectId=process.env.GCLOUD_PROJECT||process.env.GOOGLE_CLOUD_PROJECT||process.env.FIREBASE_PROJECT_ID||'morninginv';
initializeApp({credential:applicationDefault(),projectId});
const db=getFirestore();
const counts={};
const writer=db.bulkWriter();
writer.onWriteError(error=>{console.error(`Failed ${error.documentRef.path} (${error.failedAttempts} attempts): ${error.message}`);return error.failedAttempts<3;});
for(const q of json){
  counts[q.category]=(counts[q.category]||0)+1;
  writer.set(db.collection('questions').doc(q.id),q);
}
await writer.close();
console.log(`Seed complete for ${projectId}: ${json.length} questions`,counts);
