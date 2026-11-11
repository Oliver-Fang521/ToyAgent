"use client";
import AnimatedSelect from "./AnimatedSelect";
import {useWorkspaceLocale} from "./WorkspaceLocale";
export default function LanguageMenu(){const {locale,setLocale,t}=useWorkspaceLocale();return <div className="language-dropdown"><AnimatedSelect value={locale} options={[{value:"zh",label:"中文"},{value:"en",label:"English"}]} label={t("界面语言")} onChange={setLocale}/></div>}
