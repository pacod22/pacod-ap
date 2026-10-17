import type { LocalizedDoc } from '$lib/legal/types';
import {
	COMPANY,
	legalName,
	address,
	lastUpdated,
	retention,
	pacodService
} from '$lib/legal/company';

const email = COMPANY.email;

export const privacy: LocalizedDoc = {
	en: {
		eyebrow: 'Legal',
		title: 'Privacy Policy',
		lead: `This policy explains what personal data ${COMPANY.brand} collects, why we collect it, and the rights you have over it under the EU General Data Protection Regulation (GDPR).`,
		metaDescription: `How ${COMPANY.brand} collects, uses, and protects your personal data.`,
		updatedLine: `Last updated: ${lastUpdated.privacy.en} · Effective: ${lastUpdated.privacy.en}`,
		sections: [
			{
				heading: '1. Who we are',
				blocks: [
					{
						k: 'p',
						html: `${legalName.en}, registered at ${address.en}, ЕИК ${COMPANY.eik} ("we", "us"), is the data controller responsible for your personal data in connection with ${COMPANY.brand}. This policy applies to ${pacodService.en}. For any privacy question, or to exercise your rights, contact us at <a href="mailto:${email}">${email}</a>.`
					}
				]
			},
			{
				heading: '2. Data we collect',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Account data</strong> — your name, email address, and password (stored hashed), held by our own sign-in service (see section 5).',
							'<strong>Content you provide</strong> — the messages, questions, and files you submit to your assistants.',
							'<strong>Usage data</strong> — pages visited, features used, approximate token usage, and timestamps, used to operate and improve the service. For signed-in users we keep per-account records of requests and AI usage (for quotas, billing and abuse prevention). Which pages you view and how long they stay on screen is measured by our own first-party tool <em>only if you allow analytics cookies</em>; it sets no cookie and shares nothing with third parties.',
							'<strong>Technical data</strong> — IP address, browser type, and device information collected in server logs for security and diagnostics.',
							'<strong>Account activity and IP addresses</strong> — for signed-in users we keep a log of account activity (sign-ins and sign-outs, password changes, the chats you use and when, with the IP address each action came from) and a list of the IP addresses your account has used, with how often and when each was last used. We use it to keep your account secure and to detect and investigate abuse; it is visible only to our administrators.',
							'<strong>Cookies</strong> — see our <a href="/cookies">Cookie Policy</a> for the full list.'
						]
					}
				]
			},
			{
				heading: '3. How and why we use your data (legal bases)',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Purpose', 'Legal basis (GDPR Art. 6)'],
						rows: [
							[
								'Providing your account and delivering assistant responses',
								'Performance of a contract'
							],
							['Keeping the service secure and preventing abuse', 'Legitimate interests'],
							['Improving features and quality', 'Legitimate interests'],
							['Sending the newsletter you subscribed to', 'Consent'],
							['Meeting legal and accounting obligations', 'Legal obligation']
						]
					}
				]
			},
			{
				heading: '4. Sharing and processors',
				blocks: [
					{
						k: 'p',
						html: `We do not sell your personal data. We share it only with service providers ("processors") who help us run ${COMPANY.brand}, under contracts that require them to protect it and to act only on our instructions. By category, these are:`
					},
					{
						k: 'ul',
						items: [
							'a cloud hosting and infrastructure provider;',
							'an AI/LLM model provider used to generate responses;',
							'an email-delivery provider used to send the newsletter.'
						]
					},
					{
						k: 'p',
						html: 'Depending on configuration, the AI-model and web-search providers may include Google (Gemini API, Programmable Search Engine). They receive the content of your request — your messages, any images or files you attach, and the relevant conversation context — and search queries derived from it, but never the account details held by our sign-in service (section 5).'
					},
					{
						k: 'p',
						html: 'Analytics and advertising providers — Google, Yandex, Microsoft, Meta, TikTok, X, Pinterest and LinkedIn — receive data only if you allow the corresponding optional cookie category; until then their scripts are never loaded. See the <a href="/cookies">Cookie Policy</a> for exactly what each one sets. We use no third-party error-monitoring provider. How Google uses the data its analytics and advertising tags collect is explained in <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">How Google uses information from sites or apps that use our services</a>.'
					}
				]
			},
			{
				heading: '5. Your account and sign-in',
				blocks: [
					{
						k: 'p',
						html: `Accounts and sign-in for ${pacodService.en} are handled by our own sign-in service, pacod-id, which we operate ourselves at <code>id.${COMPANY.domain}</code>; it is not a third-party service.`
					},
					{
						k: 'ul',
						items: [
							'<strong>What the sign-in service stores</strong> — your email address and name, a hash of your password (never the password itself), your sign-in sessions and the devices you have signed in from, and an audit log of security events on your account.',
							'<strong>What this app keeps</strong> — a local copy of your account ID, email address and name.',
							'<strong>Cookies and retention</strong> — the sign-in service\'s cookies are listed in our <a href="/cookies">Cookie Policy</a>; we keep this data as described in section 7.'
						]
					}
				]
			},
			{
				heading: '6. International transfers',
				blocks: [
					{
						k: 'p',
						html: "Where a processor is located outside the European Economic Area, we ensure an adequate level of protection through mechanisms such as the European Commission's Standard Contractual Clauses or an adequacy decision."
					}
				]
			},
			{
				heading: '7. How long we keep it',
				blocks: [
					{
						k: 'p',
						html: `We keep personal data only as long as necessary for the purposes above. Account data is retained while your account is active and for ${retention.en.account} after you close it; server logs are kept for ${retention.en.logs}; the account activity log and IP-address history for ${retention.en.activity}; detailed usage-analytics records (requests, page views, AI errors) are kept for ${retention.en.analytics}; newsletter data is kept until you unsubscribe.`
					}
				]
			},
			{
				heading: '8. Your rights',
				blocks: [
					{ k: 'p', html: 'Under the GDPR you have the right to:' },
					{
						k: 'ul',
						items: [
							'access the personal data we hold about you;',
							'request correction of inaccurate data;',
							'request erasure of your data ("right to be forgotten");',
							'restrict or object to certain processing;',
							'receive your data in a portable format;',
							'withdraw consent at any time, without affecting processing carried out before withdrawal;',
							'lodge a complaint with your supervisory authority — in Bulgaria, the Commission for Personal Data Protection (CPDP / КЗЛД).'
						]
					},
					{
						k: 'p',
						html: `To exercise any of these rights, email <a href="mailto:${email}">${email}</a>. We respond within one month.`
					}
				]
			},
			{
				heading: '9. Security',
				blocks: [
					{
						k: 'p',
						html: 'We use appropriate technical and organisational measures to protect your data, including encryption in transit, hashed passwords, and access controls. No system is perfectly secure, but we work continuously to reduce risk.'
					}
				]
			},
			{
				heading: '10. Children',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} is not intended for children under 16, and we do not knowingly collect their data.`
					}
				]
			},
			{
				heading: '11. Changes to this policy',
				blocks: [
					{
						k: 'p',
						html: 'We may update this policy from time to time. We\'ll post the new version here and update the "Last updated" date; significant changes will be communicated directly where appropriate.'
					}
				]
			}
		]
	},

	bg: {
		eyebrow: 'Правна информация',
		title: 'Политика за поверителност',
		lead: `Тази политика обяснява какви лични данни събира ${COMPANY.brand}, защо ги събираме и какви права имате върху тях съгласно Общия регламент относно защитата на данните (ОРЗД) на ЕС.`,
		metaDescription: `Как ${COMPANY.brand} събира, използва и защитава Вашите лични данни.`,
		updatedLine: `Последна редакция: ${lastUpdated.privacy.bg} · В сила от: ${lastUpdated.privacy.bg}`,
		sections: [
			{
				heading: '1. Кои сме ние',
				blocks: [
					{
						k: 'p',
						html: `${legalName.bg}, с регистриран адрес ${address.bg}, ЕИК ${COMPANY.eik} („ние“, „нас“), е администраторът на лични данни, отговорен за Вашите лични данни във връзка с ${COMPANY.brand}. Тази политика се прилага за ${pacodService.bg}. За всеки въпрос относно поверителността или за да упражните правата си, свържете се с нас на <a href="mailto:${email}">${email}</a>.`
					}
				]
			},
			{
				heading: '2. Данни, които събираме',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Данни за профила</strong> — Вашето име, имейл адрес и парола (съхранявана хеширана), които се пазят от собствената ни услуга за вход (вижте раздел 5).',
							'<strong>Съдържание, което предоставяте</strong> — съобщенията, въпросите и файловете, които подавате към Вашите консултанти.',
							'<strong>Данни за използването</strong> — посетени страници, използвани функции, приблизително потребление на токени и времеви маркери, използвани за предоставяне и подобряване на услугата. За регистрираните потребители водим записи по профил за заявките и използването на ИИ (за лимити, отчитане и предотвратяване на злоупотреби). Кои страници разглеждате и колко дълго остават на екрана се измерва от наш собствен инструмент <em>само ако разрешите аналитичните бисквитки</em>; той не поставя бисквитка и не споделя нищо с трети страни.',
							'<strong>Технически данни</strong> — IP адрес, тип браузър и информация за устройството, събирани в сървърните журнали за целите на сигурността и диагностиката.',
							'<strong>Активност на профила и IP адреси</strong> — за влезлите потребители водим дневник на активността в профила (влизания и излизания, смени на парола, използваните чатове и кога, заедно с IP адреса, от който е извършено всяко действие) и списък на IP адресите, използвани от профила Ви, с броя използвания и последното използване на всеки от тях. Използваме ги, за да пазим профила Ви и да откриваме и разследваме злоупотреби; достъп до тях имат само нашите администратори.',
							'<strong>Бисквитки</strong> — вижте нашата <a href="/cookies">Политика за бисквитките</a> за пълния списък.'
						]
					}
				]
			},
			{
				heading: '3. Как и защо използваме Вашите данни (правни основания)',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Цел', 'Правно основание (чл. 6 от ОРЗД)'],
						rows: [
							[
								'Предоставяне на Вашия профил и доставяне на отговорите на консултантите',
								'Изпълнение на договор'
							],
							[
								'Поддържане на сигурността на услугата и предотвратяване на злоупотреби',
								'Законни интереси'
							],
							['Подобряване на функциите и качеството', 'Законни интереси'],
							['Изпращане на бюлетина, за който сте се абонирали', 'Съгласие'],
							['Изпълнение на законови и счетоводни задължения', 'Законово задължение']
						]
					}
				]
			},
			{
				heading: '4. Споделяне и обработващи',
				blocks: [
					{
						k: 'p',
						html: `Ние не продаваме Вашите лични данни. Споделяме ги само с доставчици на услуги („обработващи“), които ни помагат да управляваме ${COMPANY.brand}, съгласно договори, които ги задължават да ги защитават и да действат само по наши указания. По категории това са:`
					},
					{
						k: 'ul',
						items: [
							'доставчик на облачен хостинг и инфраструктура;',
							'доставчик на модел с изкуствен интелект/LLM, използван за генериране на отговори;',
							'доставчик на услуги за изпращане на имейли, използван за разпращане на бюлетина.'
						]
					},
					{
						k: 'p',
						html: 'В зависимост от конфигурацията доставчиците на ИИ модели и уеб търсене може да включват Google (Gemini API, Programmable Search Engine). Те получават съдържанието на заявката Ви — Вашите съобщения, прикачените изображения или файлове и съответния контекст на разговора — и производни от нея заявки за търсене, но никога данните за профила Ви, съхранявани от услугата ни за вход (раздел 5).'
					},
					{
						k: 'p',
						html: 'Доставчиците на анализ и реклама — Google, Yandex, Microsoft, Meta, TikTok, X, Pinterest и LinkedIn — получават данни само ако разрешите съответната незадължителна категория бисквитки; дотогава скриптовете им изобщо не се зареждат. Вижте <a href="/cookies">Политиката за бисквитки</a> за това какво поставя всеки от тях. Не използваме доставчик на мониторинг на грешки на трета страна. Как Google използва данните, събирани от неговите инструменти за анализ и реклама, е обяснено в <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">„Как Google използва информацията от сайтове или приложения, които използват услугите ни“</a>.'
					}
				]
			},
			{
				heading: '5. Вашият профил и вход',
				blocks: [
					{
						k: 'p',
						html: `Профилите и входът в ${pacodService.bg} се управляват от собствената ни услуга за вход pacod-id, която поддържаме сами на <code>id.${COMPANY.domain}</code>; тя не е услуга на трета страна.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Какво съхранява услугата за вход</strong> — Вашия имейл адрес и име, хеш на паролата Ви (никога самата парола), сесиите Ви на вход и устройствата, от които сте влизали, както и журнал на събитията, свързани със сигурността на профила Ви.',
							'<strong>Какво пази това приложение</strong> — локално копие на идентификатора на профила Ви, имейл адреса и името Ви.',
							'<strong>Бисквитки и срок на съхранение</strong> — бисквитките на услугата за вход са посочени в нашата <a href="/cookies">Политика за бисквитките</a>; съхраняваме тези данни, както е описано в раздел 7.'
						]
					}
				]
			},
			{
				heading: '6. Международни трансфери',
				blocks: [
					{
						k: 'p',
						html: 'Когато обработващ е разположен извън Европейското икономическо пространство, ние осигуряваме адекватно ниво на защита чрез механизми като Стандартните договорни клаузи на Европейската комисия или решение относно адекватното ниво на защита.'
					}
				]
			},
			{
				heading: '7. Колко дълго ги съхраняваме',
				blocks: [
					{
						k: 'p',
						html: `Съхраняваме личните данни само толкова дълго, колкото е необходимо за посочените по-горе цели. Данните за профила се съхраняват, докато профилът Ви е активен, и за срок от ${retention.bg.account} след неговото закриване; сървърните журнали се съхраняват за срок от ${retention.bg.logs}; дневникът на активността в профила и историята на IP адресите — за срок от ${retention.bg.activity}; подробните записи за използването (заявки, прегледи на страници, грешки на ИИ) се съхраняват за срок от ${retention.bg.analytics}; данните за бюлетина се съхраняват, докато не се отпишете.`
					}
				]
			},
			{
				heading: '8. Вашите права',
				blocks: [
					{ k: 'p', html: 'Съгласно ОРЗД имате право:' },
					{
						k: 'ul',
						items: [
							'на достъп до личните данни, които съхраняваме за Вас;',
							'да поискате коригиране на неточни данни;',
							'да поискате изтриване на данните си („право да бъдете забравени“);',
							'да ограничите или да възразите срещу определено обработване;',
							'да получите данните си в преносим формат;',
							'да оттеглите съгласието си по всяко време, без това да засяга обработването, извършено преди оттеглянето;',
							'да подадете жалба до надзорния орган — в България това е Комисията за защита на личните данни (КЗЛД / CPDP).'
						]
					},
					{
						k: 'p',
						html: `За да упражните някое от тези права, пишете на <a href="mailto:${email}">${email}</a>. Отговаряме в срок от един месец.`
					}
				]
			},
			{
				heading: '9. Сигурност',
				blocks: [
					{
						k: 'p',
						html: 'Прилагаме подходящи технически и организационни мерки за защита на Вашите данни, включително криптиране при пренос, хеширани пароли и контрол на достъпа. Никоя система не е напълно защитена, но ние работим непрекъснато за намаляване на риска.'
					}
				]
			},
			{
				heading: '10. Деца',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} не е предназначен за деца под 16 години и ние не събираме съзнателно техни данни.`
					}
				]
			},
			{
				heading: '11. Промени в тази политика',
				blocks: [
					{
						k: 'p',
						html: 'Може периодично да актуализираме тази политика. Ще публикуваме новата версия тук и ще актуализираме датата „Последна редакция“; за съществените промени ще Ви уведомяваме директно, когато това е подходящо.'
					}
				]
			}
		]
	},

	de: {
		eyebrow: 'Rechtliches',
		title: 'Datenschutzerklärung',
		lead: `Diese Erklärung beschreibt, welche personenbezogenen Daten ${COMPANY.brand} erhebt, warum wir sie erheben und welche Rechte Ihnen daran nach der Datenschutz-Grundverordnung (DSGVO) der EU zustehen.`,
		metaDescription: `Wie ${COMPANY.brand} Ihre personenbezogenen Daten erhebt, verwendet und schützt.`,
		updatedLine: `Zuletzt aktualisiert: ${lastUpdated.privacy.de} · Gültig ab: ${lastUpdated.privacy.de}`,
		sections: [
			{
				heading: '1. Wer wir sind',
				blocks: [
					{
						k: 'p',
						html: `${legalName.de}, eingetragen unter ${address.de}, ЕИК (UIC) ${COMPANY.eik} („wir“, „uns“), ist der für Ihre personenbezogenen Daten im Zusammenhang mit ${COMPANY.brand} verantwortliche Verantwortliche. Diese Erklärung gilt für ${pacodService.de}. Bei Fragen zum Datenschutz oder zur Ausübung Ihrer Rechte kontaktieren Sie uns unter <a href="mailto:${email}">${email}</a>.`
					}
				]
			},
			{
				heading: '2. Welche Daten wir erheben',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Kontodaten</strong> — Ihr Name, Ihre E-Mail-Adresse und Ihr Passwort (gehasht gespeichert), verwaltet von unserem eigenen Anmeldedienst (siehe Abschnitt 5).',
							'<strong>Von Ihnen bereitgestellte Inhalte</strong> — die Nachrichten, Fragen und Dateien, die Sie an Ihre Berater übermitteln.',
							'<strong>Nutzungsdaten</strong> — besuchte Seiten, genutzte Funktionen, ungefährer Token-Verbrauch und Zeitstempel, die dem Betrieb und der Verbesserung des Dienstes dienen. Für angemeldete Nutzer führen wir kontobezogene Aufzeichnungen über Anfragen und KI-Nutzung (für Kontingente, Abrechnung und Missbrauchsvermeidung). Welche Seiten Sie aufrufen und wie lange sie sichtbar sind, misst unser eigenes First-Party-Tool <em>nur, wenn Sie Analyse-Cookies erlauben</em>; es setzt kein Cookie und gibt nichts an Dritte weiter.',
							'<strong>Technische Daten</strong> — IP-Adresse, Browsertyp und Geräteinformationen, die zu Sicherheits- und Diagnosezwecken in Serverprotokollen erfasst werden.',
							'<strong>Kontoaktivität und IP-Adressen</strong> — für angemeldete Nutzer führen wir ein Protokoll der Kontoaktivität (An- und Abmeldungen, Passwortänderungen, die genutzten Chats und wann, jeweils mit der IP-Adresse, von der die Aktion ausging) sowie eine Liste der von Ihrem Konto genutzten IP-Adressen mit Häufigkeit und letzter Nutzung. Wir nutzen sie, um Ihr Konto zu schützen und Missbrauch zu erkennen und aufzuklären; einsehen können sie nur unsere Administratoren.',
							'<strong>Cookies</strong> — die vollständige Liste finden Sie in unserer <a href="/cookies">Cookie-Richtlinie</a>.'
						]
					}
				]
			},
			{
				heading: '3. Wie und warum wir Ihre Daten verwenden (Rechtsgrundlagen)',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Zweck', 'Rechtsgrundlage (Art. 6 DSGVO)'],
						rows: [
							[
								'Bereitstellung Ihres Kontos und Lieferung der Berater-Antworten',
								'Vertragserfüllung'
							],
							[
								'Aufrechterhaltung der Sicherheit des Dienstes und Missbrauchsverhinderung',
								'Berechtigte Interessen'
							],
							['Verbesserung von Funktionen und Qualität', 'Berechtigte Interessen'],
							['Versand des von Ihnen abonnierten Newsletters', 'Einwilligung'],
							['Erfüllung gesetzlicher und buchhalterischer Pflichten', 'Rechtliche Verpflichtung']
						]
					}
				]
			},
			{
				heading: '4. Weitergabe und Auftragsverarbeiter',
				blocks: [
					{
						k: 'p',
						html: `Wir verkaufen Ihre personenbezogenen Daten nicht. Wir geben sie nur an Dienstleister („Auftragsverarbeiter“) weiter, die uns beim Betrieb von ${COMPANY.brand} unterstützen, auf Grundlage von Verträgen, die sie zum Schutz der Daten und zum Handeln ausschließlich nach unseren Weisungen verpflichten. Nach Kategorien sind dies:`
					},
					{
						k: 'ul',
						items: [
							'ein Anbieter für Cloud-Hosting und Infrastruktur;',
							'ein KI-/LLM-Modellanbieter, der zur Erzeugung von Antworten eingesetzt wird;',
							'ein Anbieter für den E-Mail-Versand, der zum Versand des Newsletters eingesetzt wird.'
						]
					},
					{
						k: 'p',
						html: 'Je nach Konfiguration können zu den Anbietern von KI-Modellen und Websuche auch Google (Gemini API, Programmable Search Engine) gehören. Sie erhalten den Inhalt Ihrer Anfrage — Ihre Nachrichten, beigefügte Bilder oder Dateien und den relevanten Gesprächskontext — sowie daraus abgeleitete Suchanfragen, aber niemals die bei unserem Anmeldedienst gespeicherten Kontodaten (Abschnitt 5).'
					},
					{
						k: 'p',
						html: 'Analyse- und Werbeanbieter — Google, Yandex, Microsoft, Meta, TikTok, X, Pinterest und LinkedIn — erhalten nur dann Daten, wenn Sie die entsprechende optionale Cookie-Kategorie erlauben; bis dahin werden ihre Skripte gar nicht geladen. Was jeder einzelne setzt, steht in der <a href="/cookies">Cookie-Richtlinie</a>. Einen Drittanbieter für Fehlerüberwachung setzen wir nicht ein. Wie Google die von seinen Analyse- und Werbe-Tags erhobenen Daten verwendet, erläutert <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">„Wie Google Daten von Websites oder Apps verwendet, auf bzw. in denen unsere Dienste genutzt werden“</a>.'
					}
				]
			},
			{
				heading: '5. Ihr Konto und die Anmeldung',
				blocks: [
					{
						k: 'p',
						html: `Konten und Anmeldung für ${pacodService.de} verwaltet unser eigener Anmeldedienst pacod-id, den wir unter <code>id.${COMPANY.domain}</code> selbst betreiben; er ist kein Dienst eines Drittanbieters.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Was der Anmeldedienst speichert</strong> — Ihre E-Mail-Adresse und Ihren Namen, einen Hash Ihres Passworts (nie das Passwort selbst), Ihre Anmeldesitzungen und die Geräte, mit denen Sie sich angemeldet haben, sowie ein Protokoll sicherheitsrelevanter Ereignisse Ihres Kontos.',
							'<strong>Was diese App speichert</strong> — eine lokale Kopie Ihrer Konto-ID, Ihrer E-Mail-Adresse und Ihres Namens.',
							'<strong>Cookies und Aufbewahrung</strong> — die Cookies des Anmeldedienstes sind in unserer <a href="/cookies">Cookie-Richtlinie</a> aufgeführt; wir bewahren diese Daten wie in Abschnitt 7 beschrieben auf.'
						]
					}
				]
			},
			{
				heading: '6. Internationale Datenübermittlungen',
				blocks: [
					{
						k: 'p',
						html: 'Befindet sich ein Auftragsverarbeiter außerhalb des Europäischen Wirtschaftsraums, gewährleisten wir ein angemessenes Schutzniveau durch Mechanismen wie die Standardvertragsklauseln der Europäischen Kommission oder einen Angemessenheitsbeschluss.'
					}
				]
			},
			{
				heading: '7. Wie lange wir sie aufbewahren',
				blocks: [
					{
						k: 'p',
						html: `Wir bewahren personenbezogene Daten nur so lange auf, wie es für die oben genannten Zwecke erforderlich ist. Kontodaten werden aufbewahrt, solange Ihr Konto aktiv ist, sowie für einen Zeitraum von ${retention.de.account} nach dessen Schließung; Serverprotokolle werden für einen Zeitraum von ${retention.de.logs} aufbewahrt; das Kontoaktivitätsprotokoll und der IP-Adressverlauf für einen Zeitraum von ${retention.de.activity}; detaillierte Nutzungsanalyse-Daten (Anfragen, Seitenaufrufe, KI-Fehler) für einen Zeitraum von ${retention.de.analytics}; Newsletter-Daten werden aufbewahrt, bis Sie sich abmelden.`
					}
				]
			},
			{
				heading: '8. Ihre Rechte',
				blocks: [
					{ k: 'p', html: 'Nach der DSGVO haben Sie das Recht:' },
					{
						k: 'ul',
						items: [
							'auf Auskunft über die von uns über Sie gespeicherten personenbezogenen Daten;',
							'auf Berichtigung unrichtiger Daten;',
							'auf Löschung Ihrer Daten („Recht auf Vergessenwerden“);',
							'auf Einschränkung bestimmter Verarbeitungen oder Widerspruch dagegen;',
							'auf Erhalt Ihrer Daten in einem übertragbaren Format;',
							'Ihre Einwilligung jederzeit zu widerrufen, ohne dass die vor dem Widerruf erfolgte Verarbeitung berührt wird;',
							'eine Beschwerde bei Ihrer Aufsichtsbehörde einzureichen — in Bulgarien bei der Kommission für den Schutz personenbezogener Daten (KZLD / CPDP).'
						]
					},
					{
						k: 'p',
						html: `Um eines dieser Rechte auszuüben, schreiben Sie an <a href="mailto:${email}">${email}</a>. Wir antworten innerhalb eines Monats.`
					}
				]
			},
			{
				heading: '9. Sicherheit',
				blocks: [
					{
						k: 'p',
						html: 'Wir treffen angemessene technische und organisatorische Maßnahmen zum Schutz Ihrer Daten, darunter Verschlüsselung bei der Übertragung, gehashte Passwörter und Zugriffskontrollen. Kein System ist vollkommen sicher, doch wir arbeiten kontinuierlich daran, Risiken zu verringern.'
					}
				]
			},
			{
				heading: '10. Kinder',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} ist nicht für Kinder unter 16 Jahren bestimmt, und wir erheben deren Daten nicht wissentlich.`
					}
				]
			},
			{
				heading: '11. Änderungen dieser Erklärung',
				blocks: [
					{
						k: 'p',
						html: 'Wir können diese Erklärung von Zeit zu Zeit aktualisieren. Wir veröffentlichen die neue Fassung hier und passen das Datum „Zuletzt aktualisiert“ an; über wesentliche Änderungen informieren wir Sie gegebenenfalls direkt.'
					}
				]
			}
		]
	},

	ru: {
		eyebrow: 'Правовая информация',
		title: 'Политика конфиденциальности',
		lead: `Настоящая политика описывает, какие персональные данные собирает ${COMPANY.brand}, зачем мы их собираем и какими правами Вы обладаете в отношении них согласно Общему регламенту ЕС по защите данных (GDPR).`,
		metaDescription: `Как ${COMPANY.brand} собирает, использует и защищает Ваши персональные данные.`,
		updatedLine: `Последнее обновление: ${lastUpdated.privacy.ru} · Вступает в силу: ${lastUpdated.privacy.ru}`,
		sections: [
			{
				heading: '1. Кто мы',
				blocks: [
					{
						k: 'p',
						html: `${legalName.ru}, зарегистрированная по адресу ${address.ru}, ЕИК (UIC) ${COMPANY.eik} («мы», «нас»), является оператором персональных данных, ответственным за Ваши персональные данные в связи с ${COMPANY.brand}. Настоящая политика распространяется на ${pacodService.ru}. По любым вопросам, связанным с конфиденциальностью, или для реализации Ваших прав свяжитесь с нами по адресу <a href="mailto:${email}">${email}</a>.`
					}
				]
			},
			{
				heading: '2. Какие данные мы собираем',
				blocks: [
					{
						k: 'ul',
						items: [
							'<strong>Данные учётной записи</strong> — Ваше имя, адрес электронной почты и пароль (хранится в хешированном виде), которые хранит наш собственный сервис входа (см. раздел 5).',
							'<strong>Предоставляемый Вами контент</strong> — сообщения, вопросы и файлы, которые Вы направляете своим консультантам.',
							'<strong>Данные об использовании</strong> — посещённые страницы, используемые функции, приблизительный расход токенов и метки времени, используемые для работы и улучшения сервиса. Для зарегистрированных пользователей мы ведём записи о запросах и использовании ИИ по учётной записи (для лимитов, учёта и предотвращения злоупотреблений). Какие страницы Вы просматриваете и как долго они остаются на экране, измеряет наш собственный инструмент <em>только если Вы разрешили аналитические файлы cookie</em>; он не устанавливает cookie и ничего не передаёт третьим лицам.',
							'<strong>Технические данные</strong> — IP-адрес, тип браузера и сведения об устройстве, собираемые в журналах сервера в целях безопасности и диагностики.',
							'<strong>Активность учётной записи и IP-адреса</strong> — для вошедших пользователей мы ведём журнал активности учётной записи (входы и выходы, смены пароля, используемые чаты и время, с IP-адресом, с которого выполнено каждое действие) и список IP-адресов, использованных Вашей учётной записью, с числом использований и временем последнего использования. Мы используем их для защиты Вашей учётной записи и для выявления и расследования злоупотреблений; доступ к ним есть только у наших администраторов.',
							'<strong>Файлы cookie</strong> — полный перечень см. в нашей <a href="/cookies">Политике использования файлов cookie</a>.'
						]
					}
				]
			},
			{
				heading: '3. Как и зачем мы используем Ваши данные (правовые основания)',
				blocks: [
					{
						k: 'table',
						kind: 'cols',
						head: ['Цель', 'Правовое основание (ст. 6 GDPR)'],
						rows: [
							[
								'Предоставление Вашей учётной записи и выдача ответов консультантов',
								'Исполнение договора'
							],
							[
								'Обеспечение безопасности сервиса и предотвращение злоупотреблений',
								'Законные интересы'
							],
							['Улучшение функций и качества', 'Законные интересы'],
							['Отправка новостной рассылки, на которую Вы подписались', 'Согласие'],
							['Исполнение юридических и бухгалтерских обязанностей', 'Правовое обязательство']
						]
					}
				]
			},
			{
				heading: '4. Передача данных и обработчики',
				blocks: [
					{
						k: 'p',
						html: `Мы не продаём Ваши персональные данные. Мы передаём их только поставщикам услуг («обработчикам»), которые помогают нам обеспечивать работу ${COMPANY.brand}, на основании договоров, обязывающих их защищать данные и действовать исключительно по нашим указаниям. По категориям это:`
					},
					{
						k: 'ul',
						items: [
							'поставщик облачного хостинга и инфраструктуры;',
							'поставщик модели ИИ/LLM, используемой для формирования ответов;',
							'поставщик услуг доставки электронной почты, используемый для рассылки новостей.'
						]
					},
					{
						k: 'p',
						html: 'В зависимости от конфигурации среди поставщиков ИИ-моделей и веб-поиска может быть Google (Gemini API, Programmable Search Engine). Они получают содержание Вашего запроса — Ваши сообщения, прикреплённые изображения или файлы и соответствующий контекст разговора — и производные от него поисковые запросы, но никогда не получают данные Вашей учётной записи, хранящиеся в нашем сервисе входа (раздел 5).'
					},
					{
						k: 'p',
						html: 'Поставщики аналитики и рекламы — Google, Яндекс, Microsoft, Meta, TikTok, X, Pinterest и LinkedIn — получают данные только если Вы разрешите соответствующую необязательную категорию файлов cookie; до этого их скрипты вообще не загружаются. Что именно устанавливает каждый из них, см. в <a href="/cookies">Политике использования файлов cookie</a>. Стороннего поставщика мониторинга ошибок мы не используем. О том, как Google использует данные, собираемые его инструментами аналитики и рекламы, см. <a href="https://policies.google.com/technologies/partner-sites" target="_blank" rel="noopener">«Как Google использует информацию с сайтов и из приложений, которые используют сервисы Google»</a>.'
					}
				]
			},
			{
				heading: '5. Ваша учётная запись и вход',
				blocks: [
					{
						k: 'p',
						html: `Учётными записями и входом в ${pacodService.ru} управляет наш собственный сервис входа pacod-id, который мы сами эксплуатируем на <code>id.${COMPANY.domain}</code>; это не сторонний сервис.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Что хранит сервис входа</strong> — Ваш адрес электронной почты и имя, хеш Вашего пароля (но не сам пароль), Ваши сессии входа и устройства, с которых Вы входили, а также журнал событий безопасности Вашей учётной записи.',
							'<strong>Что хранит это приложение</strong> — локальную копию идентификатора Вашей учётной записи, адреса электронной почты и имени.',
							'<strong>Файлы cookie и сроки хранения</strong> — файлы cookie сервиса входа перечислены в нашей <a href="/cookies">Политике использования файлов cookie</a>; мы храним эти данные, как описано в разделе 7.'
						]
					}
				]
			},
			{
				heading: '6. Международная передача данных',
				blocks: [
					{
						k: 'p',
						html: 'Если обработчик находится за пределами Европейского экономического пространства, мы обеспечиваем надлежащий уровень защиты с помощью таких механизмов, как Стандартные договорные положения Европейской комиссии или решение о соответствии уровня защиты.'
					}
				]
			},
			{
				heading: '7. Как долго мы храним данные',
				blocks: [
					{
						k: 'p',
						html: `Мы храним персональные данные только в течение срока, необходимого для указанных выше целей. Данные учётной записи хранятся, пока Ваша учётная запись активна, и в течение ${retention.ru.account} после её закрытия; журналы сервера хранятся в течение ${retention.ru.logs}; журнал активности учётной записи и история IP-адресов — в течение ${retention.ru.activity}; подробные записи аналитики использования (запросы, просмотры страниц, ошибки ИИ) хранятся в течение ${retention.ru.analytics}; данные новостной рассылки хранятся до момента отписки.`
					}
				]
			},
			{
				heading: '8. Ваши права',
				blocks: [
					{ k: 'p', html: 'Согласно GDPR Вы имеете право:' },
					{
						k: 'ul',
						items: [
							'на доступ к персональным данным, которые мы храним о Вас;',
							'требовать исправления неточных данных;',
							'требовать удаления Ваших данных («право быть забытым»);',
							'ограничить определённую обработку или возразить против неё;',
							'получить Ваши данные в переносимом формате;',
							'отозвать согласие в любое время без ущерба для обработки, осуществлённой до отзыва;',
							'подать жалобу в надзорный орган — в Болгарии это Комиссия по защите персональных данных (КЗЛД / CPDP).'
						]
					},
					{
						k: 'p',
						html: `Чтобы реализовать любое из этих прав, напишите на <a href="mailto:${email}">${email}</a>. Мы отвечаем в течение одного месяца.`
					}
				]
			},
			{
				heading: '9. Безопасность',
				blocks: [
					{
						k: 'p',
						html: 'Мы применяем надлежащие технические и организационные меры для защиты Ваших данных, включая шифрование при передаче, хеширование паролей и контроль доступа. Ни одна система не является абсолютно защищённой, но мы постоянно работаем над снижением рисков.'
					}
				]
			},
			{
				heading: '10. Дети',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} не предназначен для детей младше 16 лет, и мы не собираем их данные осознанно.`
					}
				]
			},
			{
				heading: '11. Изменения настоящей политики',
				blocks: [
					{
						k: 'p',
						html: 'Мы можем время от времени обновлять настоящую политику. Мы будем размещать новую версию здесь и обновлять дату «Последнее обновление»; о существенных изменениях мы сообщим напрямую, когда это уместно.'
					}
				]
			}
		]
	}
};
