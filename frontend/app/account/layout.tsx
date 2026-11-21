import { auth } from '@/auth';
import { redirect } from 'next/navigation';
import { CampusShell } from '@/components/campus/shell';
export const dynamic='force-dynamic';
export default async function AccountLayout({children}:{children:React.ReactNode}){
 const session=await auth();if(!session?.user)redirect('/login');
 return <CampusShell user={session.user}>{children}</CampusShell>;
}
