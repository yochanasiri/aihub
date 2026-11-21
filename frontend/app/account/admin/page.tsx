import { auth } from '@/auth';
import { redirect } from 'next/navigation';
import { PageHeader } from '@/components/campus/pages';
import { ReviewAdmin } from '@/components/campus/review';
import { KnowledgeAdmin } from '@/components/campus/knowledge-admin';

export default async function Admin() {
  const session = await auth();
  if (session?.user.role !== 'Admin' || session.user.is_demo) redirect('/account');
  return <div className="information-page">
    <PageHeader eyebrow="ADMINISTRATOR" title="Knowledge Base">Upload institute documents, review their extracted text, and test how the assistant retrieves evidence.</PageHeader>
    <div className="knowledge-admin"><ReviewAdmin /></div>
    <KnowledgeAdmin />
  </div>;
}
