import type { LocalizedDoc } from '$lib/legal/types';
import { COMPANY, lastUpdated } from '$lib/legal/company';

const email = COMPANY.email;

export const cookies: LocalizedDoc = {
	en: {
		eyebrow: 'Legal',
		title: 'Cookie Policy',
		lead: `This policy explains what cookies are, which ones ${COMPANY.brand} uses, and how you can control them. It complements our <a href="/privacy">Privacy Policy</a>.`,
		metaDescription: `The cookies ${COMPANY.brand} uses and why.`,
		updatedLine: `Last updated: ${lastUpdated.cookies.en}`,
		sections: [
			{
				heading: '1. What are cookies?',
				blocks: [
					{
						k: 'p',
						html: "Cookies are small text files a website stores on your device. They let a site remember things between requests — for example, that you're signed in or which language you prefer. Some \"cookie-like\" storage (such as your browser's <code>localStorage</code>) works similarly but isn't technically a cookie; we note where we use it below."
					}
				]
			},
			{
				heading: '2. How we categorise cookies',
				blocks: [
					{
						k: 'ul',
						items: [
							"<strong>Strictly necessary</strong> — required for the site to work (e.g. keeping you signed in). These can't be switched off and don't require consent.",
							'<strong>Functional / preference</strong> — remember choices you make, like your language or theme, to improve your experience.',
							'<strong>Analytics</strong> — help us understand which pages are used and for how long (for signed-in users, linked to your account). Optional, off by default, and set only after you allow them. Allowing them also enables our own first-party measurement of page views and time on page, which sets no cookie of its own.',
							'<strong>Advertising</strong> — let us measure our campaigns and show relevant ads on Google, Microsoft (Bing), Facebook, Instagram, TikTok, X, Pinterest and LinkedIn. A separate choice from analytics, also off by default.'
						]
					}
				]
			},
			{
				heading: '3. Cookies we use',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Name', 'Category', 'Purpose', 'Duration'],
						rows: [
							[
								'<code>app_session</code>',
								'Strictly necessary',
								'Keeps you signed in to this site. Holds only an opaque, random session ID; your sign-in tokens stay on our server. HttpOnly, SameSite=Lax, Secure; over https it is named <code>__Host-app_session</code>.',
								'Up to 30 days while your sign-in can be renewed (otherwise until its access expires)'
							],
							[
								'<code>pid_login</code>',
								'Strictly necessary',
								'Set only while you sign in: a sealed record of the sign-in in progress — where to return afterwards and the one-time values that protect it. Removed when you come back. Path=/auth, HttpOnly, SameSite=Lax, Secure.',
								'10 minutes'
							],
							[
								'<code>webui_sso</code>',
								'Strictly necessary',
								"Signals that single sign-on to our chat app (Open-WebUI, on its own address) has been completed, so you aren't redirected in a loop. HttpOnly, SameSite=Lax, Secure.",
								'~8 hours'
							],
							[
								'<code>PARAGLIDE_LOCALE</code>',
								'Functional / preference',
								'Remembers your chosen interface language (Bulgarian, English, German, or Russian).',
								'~400 days'
							],
							[
								'<code>theme</code> <em>(localStorage, not a cookie)</em>',
								'Functional / preference',
								"Stores your light/dark theme choice in your browser so the site doesn't flash on load. Never sent to our servers.",
								'Until you clear it'
							],
							[
								'<code>cookie_consent</code>',
								'Strictly necessary',
								"Remembers your cookie choices so we don't ask again on every visit.",
								'~180 days'
							],
							[
								'<code>_ga</code>, <code>_ga_*</code>',
								'Analytics',
								'Google Analytics. Distinguishes visitors so page views can be counted in aggregate. Set only with your analytics consent.',
								'up to ~2 years'
							],
							[
								'<code>_gcl_au</code>',
								'Advertising',
								'Google Ads conversion measurement — tells us an ad led to a signup. Set only with your advertising consent.',
								'~90 days'
							],
							[
								'<code>_fbp</code>, <code>_fbc</code>',
								'Advertising',
								'Meta pixel (Facebook and Instagram). Measures campaigns and enables remarketing. Set only with your advertising consent.',
								'~90 days'
							],
							[
								'<code>_ttp</code>',
								'Advertising',
								'TikTok pixel. Measures campaigns run on TikTok. Set only with your advertising consent.',
								'~13 months'
							],
							[
								'<code>personalization_id</code>',
								'Advertising',
								'X (Twitter) website tag. Measures campaigns run on X. Set only with your advertising consent.',
								'~2 years'
							],
							[
								'<code>_ym_uid</code>, <code>_ym_d</code>, <code>_ym_*</code>',
								'Analytics',
								'Yandex Metrica. Distinguishes visitors so page views and goals can be counted in aggregate. Session recording (Webvisor) is off. Set only with your analytics consent.',
								'up to ~1 year'
							],
							[
								'<code>_clck</code>, <code>_clsk</code>',
								'Analytics',
								'Microsoft Clarity. Aggregated click and scroll heatmaps showing how pages are used. Set only with your analytics consent.',
								'up to ~1 year'
							],
							[
								'<code>_uetsid</code>, <code>_uetvid</code>',
								'Advertising',
								'Microsoft Advertising (Bing) UET tag. Measures campaigns run on Microsoft Advertising. Set only with your advertising consent.',
								'up to ~13 months'
							],
							[
								'<code>_pin_unauth</code>, <code>_epik</code>, <code>_derived_epik</code>',
								'Advertising',
								'Pinterest tag. Measures campaigns run on Pinterest. Set only with your advertising consent.',
								'up to ~1 year'
							],
							[
								'<code>li_fat_id</code>',
								'Advertising',
								'LinkedIn Insight tag. Measures campaigns run on LinkedIn. Set only with your advertising consent.',
								'~30 days'
							]
						]
					},
					{
						k: 'p',
						html: `You sign in on our own sign-in service at <code>id.${COMPANY.domain}</code> — operated by us, not by a third party. It sets these cookies on its own address, not on this site (over https, <code>pid_sid</code>, <code>pid_dev</code> and <code>pid_flow</code> are also Secure and named with the <code>__Host-</code> prefix):`
					},
					{
						k: 'table',
						kind: 'cols',
						head: ['Name', 'Category', 'Purpose', 'Duration'],
						rows: [
							[
								'<code>pid_sid</code>',
								'Strictly necessary',
								'Your sign-in session on the sign-in service. HttpOnly, SameSite=Lax.',
								'Until you close your browser, or 7 days with "Keep me signed in"'
							],
							[
								'<code>pid_dev</code>',
								'Strictly necessary',
								'Recognises this browser, so that a sign-in from a new device can be noticed. HttpOnly, SameSite=Lax.',
								'1 year'
							],
							[
								'<code>pid_flow</code>',
								'Strictly necessary',
								'Set only while you sign in: ties the sign-in in progress to this browser. HttpOnly, SameSite=Lax.',
								'15 minutes'
							],
							[
								'<code>pid_lang</code>, <code>pid_theme</code>',
								'Functional / preference',
								"Remember the language and the light/dark theme you chose on the sign-in service's pages. SameSite=Lax.",
								'1 year'
							]
						]
					}
				]
			},
			{
				heading: '4. Third-party cookies',
				blocks: [
					{
						k: 'p',
						html: `Unless you allow the optional categories above, ${COMPANY.brand} sets <strong>no third-party cookies</strong> at all — the scripts that would set them are never loaded. If you do consent, the third parties involved are Google (Analytics and Ads), Yandex (Metrica), Microsoft (Clarity and Advertising), Meta (Facebook and Instagram), TikTok, X, Pinterest and LinkedIn, each listed in the table above. We set no embedded-media or payment cookies in any case.`
					},
					{
						k: 'p',
						html: `Our sign-in service at <code>id.${COMPANY.domain}</code> is our own, not a third party; its cookies are listed in section 3, and what it stores is described in section 5 of our <a href="/privacy">Privacy Policy</a>. How Google uses information from sites that use its services, including its analytics and advertising tags: <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">policies.google.com/technologies/partner-sites</a>.`
					}
				]
			},
			{
				heading: '5. Managing cookies',
				blocks: [
					{
						k: 'p',
						html: 'You can change your choice at any time with the button below — withdrawing consent clears the optional cookies we can reach and reloads the page so nothing further is loaded. Blocking the strictly necessary and preference cookies through your browser instead may stop the site working or reset your choices; you can delete them and clear <code>localStorage</code> from your browser settings at any time.'
					},
					{ k: 'manage' }
				]
			},
			{
				heading: '6. Changes to this policy',
				blocks: [
					{
						k: 'p',
						html: 'We\'ll update this page whenever our use of cookies changes and revise the "Last updated" date accordingly.'
					}
				]
			},
			{
				heading: '7. Contact',
				blocks: [
					{
						k: 'p',
						html: `Questions about cookies? See our <a href="/contact">contact page</a> or email <a href="mailto:${email}">${email}</a>.`
					}
				]
			}
		]
	},

	bg: {
		eyebrow: 'Правна информация',
		title: 'Политика за бисквитките',
		lead: `Настоящата политика обяснява какво представляват бисквитките, кои от тях използва ${COMPANY.brand} и как можете да ги управлявате. Тя допълва нашата <a href="/privacy">Политика за поверителност</a>.`,
		metaDescription: `Бисквитките, които ${COMPANY.brand} използва, и защо.`,
		updatedLine: `Последна редакция: ${lastUpdated.cookies.bg}`,
		sections: [
			{
				heading: '1. Какво представляват бисквитките?',
				blocks: [
					{
						k: 'p',
						html: 'Бисквитките са малки текстови файлове, които уебсайтът съхранява на Вашето устройство. Те позволяват на сайта да запомня определени неща между заявките — например че сте влезли в профила си или кой език предпочитате. Някои подобни на бисквитки способи за съхранение (като <code>localStorage</code> на Вашия браузър) работят по сходен начин, но технически не са бисквитки; отбелязваме къде ги използваме по-долу.'
					}
				]
			},
			{
				heading: '2. Как категоризираме бисквитките',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Строго необходими</strong> — нужни са, за да работи сайтът (напр. за да останете вписани в профила си). Те не могат да бъдат изключени и не изискват съгласие.',
							'<strong>Функционални / за предпочитания</strong> — запомнят направените от Вас избори, като език или тема, за да подобрят Вашето изживяване.',
							'<strong>Аналитични</strong> — помагат ни да разберем кои страници се използват и колко дълго (за влезлите потребители — свързано с профила Ви). Незадължителни, изключени по подразбиране и се задават само след Вашето разрешение. Разрешаването им включва и нашето собствено измерване на прегледите на страници и времето на страница, което не поставя собствена бисквитка.',
							'<strong>Рекламни</strong> — позволяват да измерваме кампаниите си и да показваме подходящи реклами в Google, Microsoft (Bing), Facebook, Instagram, TikTok, X, Pinterest и LinkedIn. Отделен избор от аналитичните, също изключен по подразбиране.'
						]
					}
				]
			},
			{
				heading: '3. Бисквитки, които използваме',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Име', 'Категория', 'Предназначение', 'Продължителност'],
						rows: [
							[
								'<code>app_session</code>',
								'Строго необходима',
								'Поддържа Ви вписани в този сайт. Съдържа само непрозрачен, случаен идентификатор на сесия; токените за вход остават на нашия сървър. HttpOnly, SameSite=Lax, Secure; при https се нарича <code>__Host-app_session</code>.',
								'До 30 дни, докато входът Ви може да се подновява (иначе — докато изтече достъпът)'
							],
							[
								'<code>pid_login</code>',
								'Строго необходима',
								'Поставя се само докато влизате: запечатан запис на започнатия вход — къде да се върнете след това и еднократните стойности, които го защитават. Изтрива се, когато се върнете. Path=/auth, HttpOnly, SameSite=Lax, Secure.',
								'10 минути'
							],
							[
								'<code>webui_sso</code>',
								'Строго необходима',
								'Сигнализира, че единното вписване (SSO) в нашето чат приложение (Open-WebUI, на собствен адрес) е завършено, за да не бъдете пренасочвани в цикъл. HttpOnly, SameSite=Lax, Secure.',
								'~8 часа'
							],
							[
								'<code>PARAGLIDE_LOCALE</code>',
								'Функционална / за предпочитания',
								'Запомня избрания от Вас език на интерфейса (български, английски, немски или руски).',
								'~400 дни'
							],
							[
								'<code>theme</code> <em>(localStorage, а не бисквитка)</em>',
								'Функционална / за предпочитания',
								'Съхранява избора Ви на светла/тъмна тема във Вашия браузър, за да не премигва сайтът при зареждане. Никога не се изпраща към нашите сървъри.',
								'Докато не я изтриете'
							],
							[
								'<code>cookie_consent</code>',
								'Строго необходима',
								'Запомня Вашите избори относно бисквитките, за да не Ви питаме отново при всяко посещение.',
								'~180 дни'
							],
							[
								'<code>_ga</code>, <code>_ga_*</code>',
								'Аналитична',
								'Google Analytics. Различава посетителите, за да се броят прегледите обобщено. Задава се само при съгласие за аналитични бисквитки.',
								'до ~2 години'
							],
							[
								'<code>_gcl_au</code>',
								'Рекламна',
								'Измерване на реализациите в Google Ads — показва ни, че реклама е довела до регистрация. Задава се само при съгласие за рекламни бисквитки.',
								'~90 дни'
							],
							[
								'<code>_fbp</code>, <code>_fbc</code>',
								'Рекламна',
								'Пиксел на Meta (Facebook и Instagram). Измерва кампании и позволява ремаркетинг. Задава се само при съгласие за рекламни бисквитки.',
								'~90 дни'
							],
							[
								'<code>_ttp</code>',
								'Рекламна',
								'Пиксел на TikTok. Измерва кампаниите в TikTok. Задава се само при съгласие за рекламни бисквитки.',
								'~13 месеца'
							],
							[
								'<code>personalization_id</code>',
								'Рекламна',
								'Уеб таг на X (Twitter). Измерва кампаниите в X. Задава се само при съгласие за рекламни бисквитки.',
								'~2 години'
							],
							[
								'<code>_ym_uid</code>, <code>_ym_d</code>, <code>_ym_*</code>',
								'Аналитична',
								'Yandex Metrica. Различава посетителите, за да се броят прегледите и целите обобщено. Записът на сесии (Webvisor) е изключен. Задава се само при съгласие за аналитични бисквитки.',
								'до ~1 година'
							],
							[
								'<code>_clck</code>, <code>_clsk</code>',
								'Аналитична',
								'Microsoft Clarity. Обобщени топлинни карти на кликванията и превъртането, които показват как се използват страниците. Задава се само при съгласие за аналитични бисквитки.',
								'до ~1 година'
							],
							[
								'<code>_uetsid</code>, <code>_uetvid</code>',
								'Рекламна',
								'UET таг на Microsoft Advertising (Bing). Измерва кампаниите в Microsoft Advertising. Задава се само при съгласие за рекламни бисквитки.',
								'до ~13 месеца'
							],
							[
								'<code>_pin_unauth</code>, <code>_epik</code>, <code>_derived_epik</code>',
								'Рекламна',
								'Таг на Pinterest. Измерва кампаниите в Pinterest. Задава се само при съгласие за рекламни бисквитки.',
								'до ~1 година'
							],
							[
								'<code>li_fat_id</code>',
								'Рекламна',
								'LinkedIn Insight таг. Измерва кампаниите в LinkedIn. Задава се само при съгласие за рекламни бисквитки.',
								'~30 дни'
							]
						]
					},
					{
						k: 'p',
						html: `Влизате чрез собствената ни услуга за вход на <code>id.${COMPANY.domain}</code>, която се поддържа от нас, а не от трета страна. Тя поставя следните бисквитки на собствения си адрес, а не на този сайт (при https <code>pid_sid</code>, <code>pid_dev</code> и <code>pid_flow</code> са и Secure и имената им започват с <code>__Host-</code>):`
					},
					{
						k: 'table',
						kind: 'cols',
						head: ['Име', 'Категория', 'Предназначение', 'Продължителност'],
						rows: [
							[
								'<code>pid_sid</code>',
								'Строго необходима',
								'Сесията Ви в услугата за вход. HttpOnly, SameSite=Lax.',
								'Докато затворите браузъра или 7 дни при „Запомни ме“'
							],
							[
								'<code>pid_dev</code>',
								'Строго необходима',
								'Разпознава този браузър, за да може да се забележи вход от ново устройство. HttpOnly, SameSite=Lax.',
								'1 година'
							],
							[
								'<code>pid_flow</code>',
								'Строго необходима',
								'Поставя се само докато влизате: свързва започнатия вход с този браузър. HttpOnly, SameSite=Lax.',
								'15 минути'
							],
							[
								'<code>pid_lang</code>, <code>pid_theme</code>',
								'Функционална / за предпочитания',
								'Запомнят избрания от Вас език и светла/тъмна тема на страниците на услугата за вход. SameSite=Lax.',
								'1 година'
							]
						]
					}
				]
			},
			{
				heading: '4. Бисквитки на трети страни',
				blocks: [
					{
						k: 'p',
						html: `Ако не разрешите незадължителните категории по-горе, ${COMPANY.brand} <strong>не поставя никакви бисквитки на трети страни</strong> — скриптовете, които биха ги поставили, изобщо не се зареждат. При дадено съгласие третите страни са Google (Analytics и Ads), Yandex (Metrica), Microsoft (Clarity и Advertising), Meta (Facebook и Instagram), TikTok, X, Pinterest и LinkedIn, всяка от които е посочена в таблицата по-горе. Бисквитки за вградено съдържание или разплащания не поставяме при никакви обстоятелства.`
					},
					{
						k: 'p',
						html: `Услугата ни за вход на <code>id.${COMPANY.domain}</code> е наша собствена, а не на трета страна; бисквитките ѝ са посочени в раздел 3, а какво съхранява тя е описано в раздел 5 от нашата <a href="/privacy">Политика за поверителност</a>. Как Google използва информацията от сайтове, които използват услугите му, включително инструментите му за анализ и реклама: <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">policies.google.com/technologies/partner-sites</a>.`
					}
				]
			},
			{
				heading: '5. Управление на бисквитките',
				blocks: [
					{
						k: 'p',
						html: 'Можете да промените избора си по всяко време с бутона по-долу — оттеглянето на съгласието изчиства незадължителните бисквитки, до които имаме достъп, и презарежда страницата, за да не се зарежда нищо повече. Блокирането на строго необходимите бисквитки и тези за предпочитания през браузъра може да наруши работата на сайта или да нулира изборите Ви; можете да ги изтривате и да изчиствате <code>localStorage</code> от настройките на браузъра си по всяко време.'
					},
					{ k: 'manage' }
				]
			},
			{
				heading: '6. Промени в настоящата политика',
				blocks: [
					{
						k: 'p',
						html: 'Ще актуализираме тази страница винаги когато използването на бисквитки от наша страна се промени, и ще обновяваме съответно датата „Последна редакция“.'
					}
				]
			},
			{
				heading: '7. Контакт',
				blocks: [
					{
						k: 'p',
						html: `Въпроси относно бисквитките? Вижте нашата <a href="/contact">страница за контакт</a> или ни пишете на <a href="mailto:${email}">${email}</a>.`
					}
				]
			}
		]
	},

	de: {
		eyebrow: 'Rechtliches',
		title: 'Cookie-Richtlinie',
		lead: `Diese Richtlinie erläutert, was Cookies sind, welche ${COMPANY.brand} verwendet und wie Sie sie steuern können. Sie ergänzt unsere <a href="/privacy">Datenschutzerklärung</a>.`,
		metaDescription: `Die von ${COMPANY.brand} verwendeten Cookies und warum.`,
		updatedLine: `Zuletzt aktualisiert: ${lastUpdated.cookies.de}`,
		sections: [
			{
				heading: '1. Was sind Cookies?',
				blocks: [
					{
						k: 'p',
						html: 'Cookies sind kleine Textdateien, die eine Website auf Ihrem Gerät speichert. Sie ermöglichen es einer Website, sich Dinge zwischen Anfragen zu merken — zum Beispiel, dass Sie angemeldet sind oder welche Sprache Sie bevorzugen. Manche „cookie-ähnlichen“ Speicher (wie der <code>localStorage</code> Ihres Browsers) funktionieren ähnlich, sind aber technisch gesehen keine Cookies; wo wir sie einsetzen, weisen wir nachstehend darauf hin.'
					}
				]
			},
			{
				heading: '2. Wie wir Cookies einordnen',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Unbedingt erforderlich</strong> — für den Betrieb der Website nötig (z. B. um Sie angemeldet zu halten). Diese können nicht abgeschaltet werden und erfordern keine Einwilligung.',
							'<strong>Funktional / Präferenz</strong> — merken sich Ihre Entscheidungen, etwa Ihre Sprache oder Ihr Design, um Ihr Erlebnis zu verbessern.',
							'<strong>Analyse</strong> — helfen uns zu verstehen, welche Seiten wie lange genutzt werden (bei angemeldeten Nutzern mit Ihrem Konto verknüpft). Optional, standardmäßig deaktiviert und nur nach Ihrer Zustimmung gesetzt. Ihre Zustimmung aktiviert außerdem unsere eigene First-Party-Messung von Seitenaufrufen und Verweildauer, die selbst kein Cookie setzt.',
							'<strong>Werbung</strong> — ermöglichen es, unsere Kampagnen zu messen und relevante Anzeigen bei Google, Microsoft (Bing), Facebook, Instagram, TikTok, X, Pinterest und LinkedIn auszuspielen. Eine von der Analyse getrennte Entscheidung, ebenfalls standardmäßig deaktiviert.'
						]
					}
				]
			},
			{
				heading: '3. Von uns verwendete Cookies',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Name', 'Kategorie', 'Zweck', 'Dauer'],
						rows: [
							[
								'<code>app_session</code>',
								'Unbedingt erforderlich',
								'Hält Sie auf dieser Website angemeldet. Enthält nur eine undurchsichtige, zufällige Sitzungs-ID; die Anmelde-Tokens bleiben auf unserem Server. HttpOnly, SameSite=Lax, Secure; über https heißt es <code>__Host-app_session</code>.',
								'Bis zu 30 Tage, solange Ihre Anmeldung erneuert werden kann (sonst bis der Zugriff abläuft)'
							],
							[
								'<code>pid_login</code>',
								'Unbedingt erforderlich',
								'Wird nur während der Anmeldung gesetzt: ein versiegelter Datensatz der laufenden Anmeldung — wohin Sie danach zurückkehren und die Einmalwerte, die sie schützen. Wird bei Ihrer Rückkehr gelöscht. Path=/auth, HttpOnly, SameSite=Lax, Secure.',
								'10 Minuten'
							],
							[
								'<code>webui_sso</code>',
								'Unbedingt erforderlich',
								'Signalisiert, dass die Single-Sign-On-Anmeldung bei unserer Chat-App (Open-WebUI, unter eigener Adresse) abgeschlossen ist, damit Sie nicht in einer Schleife weitergeleitet werden. HttpOnly, SameSite=Lax, Secure.',
								'~8 Stunden'
							],
							[
								'<code>PARAGLIDE_LOCALE</code>',
								'Funktional / Präferenz',
								'Merkt sich die von Ihnen gewählte Oberflächensprache (Bulgarisch, Englisch, Deutsch oder Russisch).',
								'~400 Tage'
							],
							[
								'<code>theme</code> <em>(localStorage, kein Cookie)</em>',
								'Funktional / Präferenz',
								'Speichert Ihre Wahl des hellen/dunklen Designs in Ihrem Browser, damit die Website beim Laden nicht flackert. Wird niemals an unsere Server gesendet.',
								'Bis Sie ihn löschen'
							],
							[
								'<code>cookie_consent</code>',
								'Unbedingt erforderlich',
								'Merkt sich Ihre Cookie-Entscheidungen, damit wir nicht bei jedem Besuch erneut fragen.',
								'~180 Tage'
							],
							[
								'<code>_ga</code>, <code>_ga_*</code>',
								'Analyse',
								'Google Analytics. Unterscheidet Besucher, damit Seitenaufrufe aggregiert gezählt werden können. Wird nur mit Ihrer Analyse-Einwilligung gesetzt.',
								'bis zu ~2 Jahre'
							],
							[
								'<code>_gcl_au</code>',
								'Werbung',
								'Google-Ads-Conversion-Messung — zeigt, dass eine Anzeige zu einer Registrierung geführt hat. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'~90 Tage'
							],
							[
								'<code>_fbp</code>, <code>_fbc</code>',
								'Werbung',
								'Meta-Pixel (Facebook und Instagram). Misst Kampagnen und ermöglicht Remarketing. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'~90 Tage'
							],
							[
								'<code>_ttp</code>',
								'Werbung',
								'TikTok-Pixel. Misst Kampagnen auf TikTok. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'~13 Monate'
							],
							[
								'<code>personalization_id</code>',
								'Werbung',
								'X-(Twitter-)Website-Tag. Misst Kampagnen auf X. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'~2 Jahre'
							],
							[
								'<code>_ym_uid</code>, <code>_ym_d</code>, <code>_ym_*</code>',
								'Analyse',
								'Yandex Metrica. Unterscheidet Besucher, damit Seitenaufrufe und Ziele aggregiert gezählt werden können. Die Sitzungsaufzeichnung (Webvisor) ist deaktiviert. Wird nur mit Ihrer Analyse-Einwilligung gesetzt.',
								'bis zu ~1 Jahr'
							],
							[
								'<code>_clck</code>, <code>_clsk</code>',
								'Analyse',
								'Microsoft Clarity. Aggregierte Klick- und Scroll-Heatmaps, die zeigen, wie Seiten genutzt werden. Wird nur mit Ihrer Analyse-Einwilligung gesetzt.',
								'bis zu ~1 Jahr'
							],
							[
								'<code>_uetsid</code>, <code>_uetvid</code>',
								'Werbung',
								'UET-Tag von Microsoft Advertising (Bing). Misst Kampagnen auf Microsoft Advertising. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'bis zu ~13 Monate'
							],
							[
								'<code>_pin_unauth</code>, <code>_epik</code>, <code>_derived_epik</code>',
								'Werbung',
								'Pinterest-Tag. Misst Kampagnen auf Pinterest. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'bis zu ~1 Jahr'
							],
							[
								'<code>li_fat_id</code>',
								'Werbung',
								'LinkedIn-Insight-Tag. Misst Kampagnen auf LinkedIn. Wird nur mit Ihrer Werbe-Einwilligung gesetzt.',
								'~30 Tage'
							]
						]
					},
					{
						k: 'p',
						html: `Sie melden sich über unseren eigenen Anmeldedienst unter <code>id.${COMPANY.domain}</code> an, den wir selbst betreiben — kein Drittanbieter. Er setzt die folgenden Cookies auf seiner eigenen Adresse, nicht auf dieser Website (über https sind <code>pid_sid</code>, <code>pid_dev</code> und <code>pid_flow</code> zudem Secure und tragen das Präfix <code>__Host-</code>):`
					},
					{
						k: 'table',
						kind: 'cols',
						head: ['Name', 'Kategorie', 'Zweck', 'Dauer'],
						rows: [
							[
								'<code>pid_sid</code>',
								'Unbedingt erforderlich',
								'Ihre Anmeldesitzung beim Anmeldedienst. HttpOnly, SameSite=Lax.',
								'Bis Sie den Browser schließen, oder 7 Tage mit „Angemeldet bleiben“'
							],
							[
								'<code>pid_dev</code>',
								'Unbedingt erforderlich',
								'Erkennt diesen Browser wieder, damit eine Anmeldung von einem neuen Gerät auffällt. HttpOnly, SameSite=Lax.',
								'1 Jahr'
							],
							[
								'<code>pid_flow</code>',
								'Unbedingt erforderlich',
								'Wird nur während der Anmeldung gesetzt: verknüpft die laufende Anmeldung mit diesem Browser. HttpOnly, SameSite=Lax.',
								'15 Minuten'
							],
							[
								'<code>pid_lang</code>, <code>pid_theme</code>',
								'Funktional / Präferenz',
								'Merken sich die von Ihnen gewählte Sprache und das helle/dunkle Design auf den Seiten des Anmeldedienstes. SameSite=Lax.',
								'1 Jahr'
							]
						]
					}
				]
			},
			{
				heading: '4. Cookies von Drittanbietern',
				blocks: [
					{
						k: 'p',
						html: `Solange Sie die optionalen Kategorien oben nicht erlauben, setzt ${COMPANY.brand} <strong>überhaupt keine Cookies von Drittanbietern</strong> — die Skripte, die sie setzen würden, werden gar nicht erst geladen. Mit Ihrer Einwilligung sind die beteiligten Dritten Google (Analytics und Ads), Yandex (Metrica), Microsoft (Clarity und Advertising), Meta (Facebook und Instagram), TikTok, X, Pinterest und LinkedIn, jeweils in der Tabelle oben aufgeführt. Cookies für eingebettete Medien oder Zahlungen setzen wir in keinem Fall.`
					},
					{
						k: 'p',
						html: `Unser Anmeldedienst unter <code>id.${COMPANY.domain}</code> ist unser eigener, kein Drittanbieter; seine Cookies sind in Abschnitt 3 aufgeführt, und was er speichert, beschreibt Abschnitt 5 unserer <a href="/privacy">Datenschutzerklärung</a>. Wie Google Informationen von Websites verwendet, die seine Dienste nutzen, einschließlich seiner Analyse- und Werbe-Tags: <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">policies.google.com/technologies/partner-sites</a>.`
					}
				]
			},
			{
				heading: '5. Cookies verwalten',
				blocks: [
					{
						k: 'p',
						html: 'Sie können Ihre Wahl jederzeit über die Schaltfläche unten ändern — ein Widerruf löscht die optionalen Cookies, die wir erreichen können, und lädt die Seite neu, sodass nichts weiter geladen wird. Das Blockieren der unbedingt erforderlichen und der Präferenz-Cookies über den Browser kann dagegen dazu führen, dass die Website nicht mehr funktioniert oder Ihre Entscheidungen zurückgesetzt werden; Sie können sie jederzeit löschen und den <code>localStorage</code> in den Browsereinstellungen leeren.'
					},
					{ k: 'manage' }
				]
			},
			{
				heading: '6. Änderungen dieser Richtlinie',
				blocks: [
					{
						k: 'p',
						html: 'Wir aktualisieren diese Seite, sobald sich unsere Verwendung von Cookies ändert, und passen das Datum „Zuletzt aktualisiert“ entsprechend an.'
					}
				]
			},
			{
				heading: '7. Kontakt',
				blocks: [
					{
						k: 'p',
						html: `Fragen zu Cookies? Besuchen Sie unsere <a href="/contact">Kontaktseite</a> oder schreiben Sie uns an <a href="mailto:${email}">${email}</a>.`
					}
				]
			}
		]
	},

	ru: {
		eyebrow: 'Правовая информация',
		title: 'Политика в отношении файлов cookie',
		lead: `Настоящая политика объясняет, что такое файлы cookie, какие из них использует ${COMPANY.brand} и как Вы можете ими управлять. Она дополняет нашу <a href="/privacy">Политику конфиденциальности</a>.`,
		metaDescription: `Файлы cookie, которые использует ${COMPANY.brand}, и для чего.`,
		updatedLine: `Последнее обновление: ${lastUpdated.cookies.ru}`,
		sections: [
			{
				heading: '1. Что такое файлы cookie?',
				blocks: [
					{
						k: 'p',
						html: 'Файлы cookie — это небольшие текстовые файлы, которые веб-сайт сохраняет на Вашем устройстве. Они позволяют сайту запоминать определённые сведения между запросами — например, что Вы вошли в учётную запись или какой язык предпочитаете. Некоторые схожие с cookie способы хранения (такие как <code>localStorage</code> Вашего браузера) работают аналогично, но технически не являются файлами cookie; ниже мы отмечаем, где их используем.'
					}
				]
			},
			{
				heading: '2. Как мы классифицируем файлы cookie',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Строго необходимые</strong> — требуются для работы сайта (например, чтобы Вы оставались в системе). Их нельзя отключить, и они не требуют согласия.',
							'<strong>Функциональные / для предпочтений</strong> — запоминают сделанный Вами выбор, например язык или тему оформления, чтобы улучшить Ваше взаимодействие с сайтом.',
							'<strong>Аналитические</strong> — помогают понять, какие страницы используются и как долго (для вошедших пользователей — с привязкой к Вашей учётной записи). Необязательные, отключены по умолчанию и устанавливаются только с Вашего разрешения. Разрешение также включает наше собственное измерение просмотров страниц и времени на странице, которое само не устанавливает cookie.',
							'<strong>Рекламные</strong> — позволяют оценивать наши кампании и показывать релевантную рекламу в Google, Microsoft (Bing), Facebook, Instagram, TikTok, X, Pinterest и LinkedIn. Отдельный от аналитики выбор, также отключён по умолчанию.'
						]
					}
				]
			},
			{
				heading: '3. Используемые нами файлы cookie',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Имя', 'Категория', 'Назначение', 'Срок хранения'],
						rows: [
							[
								'<code>app_session</code>',
								'Строго необходимый',
								'Позволяет Вам оставаться в системе на этом сайте. Содержит только непрозрачный случайный идентификатор сессии; токены входа остаются на нашем сервере. HttpOnly, SameSite=Lax, Secure; по https называется <code>__Host-app_session</code>.',
								'До 30 дней, пока вход может продлеваться (иначе — до истечения доступа)'
							],
							[
								'<code>pid_login</code>',
								'Строго необходимый',
								'Устанавливается только на время входа: запечатанная запись текущего входа — куда вернуться после него и одноразовые значения, которые его защищают. Удаляется после Вашего возвращения. Path=/auth, HttpOnly, SameSite=Lax, Secure.',
								'10 минут'
							],
							[
								'<code>webui_sso</code>',
								'Строго необходимый',
								'Сигнализирует, что единый вход (SSO) в наше чат-приложение (Open-WebUI, на отдельном адресе) завершён, чтобы Вас не перенаправляло по кругу. HttpOnly, SameSite=Lax, Secure.',
								'~8 часов'
							],
							[
								'<code>PARAGLIDE_LOCALE</code>',
								'Функциональный / для предпочтений',
								'Запоминает выбранный Вами язык интерфейса (болгарский, английский, немецкий или русский).',
								'~400 дней'
							],
							[
								'<code>theme</code> <em>(localStorage, а не файл cookie)</em>',
								'Функциональный / для предпочтений',
								'Сохраняет Ваш выбор светлой/тёмной темы в браузере, чтобы сайт не мигал при загрузке. Никогда не отправляется на наши серверы.',
								'Пока Вы его не удалите'
							],
							[
								'<code>cookie_consent</code>',
								'Строго необходимый',
								'Запоминает Ваш выбор в отношении файлов cookie, чтобы мы не спрашивали Вас снова при каждом посещении.',
								'~180 дней'
							],
							[
								'<code>_ga</code>, <code>_ga_*</code>',
								'Аналитический',
								'Google Analytics. Различает посетителей, чтобы считать просмотры страниц в совокупности. Устанавливается только при согласии на аналитику.',
								'до ~2 лет'
							],
							[
								'<code>_gcl_au</code>',
								'Рекламный',
								'Измерение конверсий Google Ads — показывает, что реклама привела к регистрации. Устанавливается только при согласии на рекламу.',
								'~90 дней'
							],
							[
								'<code>_fbp</code>, <code>_fbc</code>',
								'Рекламный',
								'Пиксель Meta (Facebook и Instagram). Оценивает кампании и обеспечивает ремаркетинг. Устанавливается только при согласии на рекламу.',
								'~90 дней'
							],
							[
								'<code>_ttp</code>',
								'Рекламный',
								'Пиксель TikTok. Оценивает кампании в TikTok. Устанавливается только при согласии на рекламу.',
								'~13 месяцев'
							],
							[
								'<code>personalization_id</code>',
								'Рекламный',
								'Веб-тег X (Twitter). Оценивает кампании в X. Устанавливается только при согласии на рекламу.',
								'~2 года'
							],
							[
								'<code>_ym_uid</code>, <code>_ym_d</code>, <code>_ym_*</code>',
								'Аналитический',
								'Яндекс Метрика. Различает посетителей, чтобы просмотры и цели учитывались в обобщённом виде. Запись сессий (Вебвизор) отключена. Устанавливается только при согласии на аналитику.',
								'до ~1 года'
							],
							[
								'<code>_clck</code>, <code>_clsk</code>',
								'Аналитический',
								'Microsoft Clarity. Обобщённые тепловые карты кликов и прокрутки, показывающие, как используются страницы. Устанавливается только при согласии на аналитику.',
								'до ~1 года'
							],
							[
								'<code>_uetsid</code>, <code>_uetvid</code>',
								'Рекламный',
								'Тег UET Microsoft Advertising (Bing). Оценивает кампании в Microsoft Advertising. Устанавливается только при согласии на рекламу.',
								'до ~13 месяцев'
							],
							[
								'<code>_pin_unauth</code>, <code>_epik</code>, <code>_derived_epik</code>',
								'Рекламный',
								'Тег Pinterest. Оценивает кампании в Pinterest. Устанавливается только при согласии на рекламу.',
								'до ~1 года'
							],
							[
								'<code>li_fat_id</code>',
								'Рекламный',
								'Тег LinkedIn Insight. Оценивает кампании в LinkedIn. Устанавливается только при согласии на рекламу.',
								'~30 дней'
							]
						]
					},
					{
						k: 'p',
						html: `Вход выполняется через наш собственный сервис входа на <code>id.${COMPANY.domain}</code>, которым управляем мы сами, а не третья сторона. Он устанавливает следующие файлы cookie на собственном адресе, а не на этом сайте (по https <code>pid_sid</code>, <code>pid_dev</code> и <code>pid_flow</code> также имеют атрибут Secure и префикс <code>__Host-</code>):`
					},
					{
						k: 'table',
						kind: 'cols',
						head: ['Имя', 'Категория', 'Назначение', 'Срок хранения'],
						rows: [
							[
								'<code>pid_sid</code>',
								'Строго необходимый',
								'Ваша сессия входа в сервисе входа. HttpOnly, SameSite=Lax.',
								'До закрытия браузера или 7 дней, если выбрано «Не выходить из системы»'
							],
							[
								'<code>pid_dev</code>',
								'Строго необходимый',
								'Распознаёт этот браузер, чтобы замечать входы с новых устройств. HttpOnly, SameSite=Lax.',
								'1 год'
							],
							[
								'<code>pid_flow</code>',
								'Строго необходимый',
								'Устанавливается только на время входа: связывает текущий вход с этим браузером. HttpOnly, SameSite=Lax.',
								'15 минут'
							],
							[
								'<code>pid_lang</code>, <code>pid_theme</code>',
								'Функциональный / для предпочтений',
								'Запоминают выбранные Вами язык и светлую/тёмную тему на страницах сервиса входа. SameSite=Lax.',
								'1 год'
							]
						]
					}
				]
			},
			{
				heading: '4. Файлы cookie третьих сторон',
				blocks: [
					{
						k: 'p',
						html: `Пока Вы не разрешите необязательные категории выше, ${COMPANY.brand} <strong>не устанавливает никаких файлов cookie третьих сторон</strong> — скрипты, которые их устанавливали бы, вообще не загружаются. При Вашем согласии третьими сторонами являются Google (Analytics и Ads), Яндекс (Метрика), Microsoft (Clarity и Advertising), Meta (Facebook и Instagram), TikTok, X, Pinterest и LinkedIn, каждая из которых указана в таблице выше. Файлы cookie встроенных медиа или платежей мы не устанавливаем ни при каких условиях.`
					},
					{
						k: 'p',
						html: `Наш сервис входа на <code>id.${COMPANY.domain}</code> — наш собственный, а не сторонний; его файлы cookie указаны в разделе 3, а что он хранит, описано в разделе 5 нашей <a href="/privacy">Политики конфиденциальности</a>. О том, как Google использует информацию с сайтов, использующих его сервисы, включая инструменты аналитики и рекламы: <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">policies.google.com/technologies/partner-sites</a>.`
					}
				]
			},
			{
				heading: '5. Управление файлами cookie',
				blocks: [
					{
						k: 'p',
						html: 'Вы можете изменить свой выбор в любое время с помощью кнопки ниже — отзыв согласия удаляет доступные нам необязательные файлы cookie и перезагружает страницу, чтобы больше ничего не загружалось. Блокировка строго необходимых и настроечных файлов cookie через браузер, напротив, может привести к неработоспособности сайта или сбросу Вашего выбора; удалить их и очистить <code>localStorage</code> можно в настройках браузера в любое время.'
					},
					{ k: 'manage' }
				]
			},
			{
				heading: '6. Изменения настоящей политики',
				blocks: [
					{
						k: 'p',
						html: 'Мы будем обновлять эту страницу всякий раз, когда меняется наше использование файлов cookie, и соответственно изменять дату «Последнее обновление».'
					}
				]
			},
			{
				heading: '7. Контакты',
				blocks: [
					{
						k: 'p',
						html: `Вопросы о файлах cookie? См. нашу <a href="/contact">страницу контактов</a> или напишите нам на <a href="mailto:${email}">${email}</a>.`
					}
				]
			}
		]
	}
};
