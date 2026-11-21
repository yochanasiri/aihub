import type { Metadata } from "next";
import "./styles.css";
export const metadata:Metadata={title:"IIT Indore AI Campus Assistant · AIHUB",description:"Your campus knowledge network. Official IIT Indore sources, verified excerpts, and private conversations."};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body>{children}</body></html>;}
