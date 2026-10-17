/**
 * `/agents/tax` — the Tax & Accounting Assistant.
 *
 * Backed by the `taxes` profile: the platform's advanced example agent (Bulgarian
 * tax and accounting), installed with `make seed-example PROFILE=taxes`. It is
 * the one place where tax-specific marketing copy lives.
 */
import type { AgentDefinition } from './types';

export const tax: AgentDefinition = {
	slug: 'tax',
	profileId: 'taxes',
	accent: '#0e6e57',
	doc: {
		en: {
			eyebrow: 'AI assistant',
			title: 'Tax & Accounting Assistant',
			lead: 'Rates, thresholds, deadlines, VAT, invoices and bookkeeping — answered from the current Bulgarian tax and accounting rules, with the source behind every answer.',
			metaTitle: 'Bulgarian Tax & Accounting AI Assistant',
			metaDescription:
				'Ask about Bulgarian VAT, income tax, social contributions, invoicing and filing deadlines. Answers grounded in current legislation, in four languages, 24/7.',
			valuePropsTitle: 'Why people use it',
			valueProps: [
				{
					title: 'Grounded in current rules',
					body: 'Answers are retrieved from the legislation and guidance in force for the current tax year — not from a model’s memory of how things used to work.'
				},
				{
					title: 'Plain answers, with the source',
					body: 'You get a direct answer first, then the provision it rests on, so you can check it or hand it to your accountant.'
				},
				{
					title: 'Available whenever the question comes up',
					body: 'Deadlines rarely arrive during office hours. The assistant answers at any time, in Bulgarian, English, German or Russian.'
				}
			],
			useCasesTitle: 'Typical questions',
			useCases: [
				'What are this year’s VAT registration thresholds, and when must I register?',
				'Which social and health contributions apply to a self-employed person?',
				'What must a valid invoice contain, and when can I issue a credit note?',
				'When is the annual tax return due, and what happens if I file late?',
				'How is income from a second employment contract taxed?'
			],
			faqTitle: 'Frequently asked',
			faq: [
				{
					q: 'Is this legal or tax advice?',
					a: 'No. It is an information service: it explains the rules and points to the provisions behind them. For a binding opinion on your specific situation, consult a licensed accountant or tax adviser.'
				},
				{
					q: 'Which country’s rules does it cover?',
					a: 'Bulgarian tax and accounting legislation, for the current tax year.'
				},
				{
					q: 'Which languages can I ask in?',
					a: 'The interface is available in Bulgarian, English, German and Russian.'
				},
				{
					q: 'How current is the information?',
					a: 'The knowledge base is maintained against the legislation in force for the current year, and rates and deadlines are kept as structured reference data rather than free text.'
				}
			],
			ctaTitle: 'Ask your first question',
			ctaBody: 'Create an account and start a conversation — no setup, no configuration.',
			ctaLabel: 'Start a conversation',
			homeLabel: 'Home'
		},
		bg: {
			eyebrow: 'AI консултант',
			title: 'Данъци и счетоводство',
			lead: 'Ставки, прагове, срокове, ДДС, фактури и счетоводство — отговори на база актуалното българско данъчно и счетоводно законодателство, с посочен източник.',
			metaTitle: 'AI консултант по данъци и счетоводство',
			metaDescription:
				'Питайте за ДДС, данък върху доходите, осигуровки, фактуриране и срокове за деклариране. Отговори на база актуалното законодателство, на четири езика, денонощно.',
			valuePropsTitle: 'Защо се използва',
			valueProps: [
				{
					title: 'На база действащите правила',
					body: 'Отговорите се извличат от законодателството и указанията, в сила за текущата данъчна година — не от общи знания на модела.'
				},
				{
					title: 'Ясен отговор и източник',
					body: 'Първо получавате директен отговор, след това разпоредбата, на която стъпва — за да проверите или да я предадете на счетоводителя си.'
				},
				{
					title: 'Достъпен, когато въпросът възникне',
					body: 'Сроковете рядко идват в работно време. Консултантът отговаря по всяко време — на български, английски, немски или руски.'
				}
			],
			useCasesTitle: 'Типични въпроси',
			useCases: [
				'Какви са праговете за регистрация по ДДС тази година и кога съм длъжен да се регистрирам?',
				'Какви осигурителни и здравни вноски дължи самоосигуряващо се лице?',
				'Какво трябва да съдържа редовната фактура и кога се издава кредитно известие?',
				'Кога е срокът за годишната данъчна декларация и какво следва при закъснение?',
				'Как се облагат доходите по втори трудов договор?'
			],
			faqTitle: 'Често задавани въпроси',
			faq: [
				{
					q: 'Това правна или данъчна консултация ли е?',
					a: 'Не. Това е информационна услуга: обяснява правилата и посочва разпоредбите зад тях. За обвързващо становище по конкретния ви случай се обърнете към лицензиран счетоводител или данъчен консултант.'
				},
				{
					q: 'Законодателството на коя държава обхваща?',
					a: 'Българското данъчно и счетоводно законодателство за текущата данъчна година.'
				},
				{
					q: 'На какви езици мога да питам?',
					a: 'Интерфейсът е достъпен на български, английски, немски и руски.'
				},
				{
					q: 'Колко актуална е информацията?',
					a: 'Базата знания се поддържа спрямо законодателството в сила за текущата година, а ставките и сроковете се съхраняват като структурирани данни, а не като свободен текст.'
				}
			],
			ctaTitle: 'Задайте първия си въпрос',
			ctaBody: 'Създайте профил и започнете разговор — без настройки и без конфигурация.',
			ctaLabel: 'Започнете разговор',
			homeLabel: 'Начало'
		},
		de: {
			eyebrow: 'KI-Berater',
			title: 'Steuern & Buchhaltung',
			lead: 'Sätze, Schwellenwerte, Fristen, Mehrwertsteuer, Rechnungen und Buchhaltung — beantwortet auf Basis des geltenden bulgarischen Steuer- und Rechnungslegungsrechts, mit Quellenangabe.',
			metaTitle: 'KI-Berater für Steuern & Buchhaltung',
			metaDescription:
				'Fragen zu bulgarischer Mehrwertsteuer, Einkommensteuer, Sozialabgaben, Rechnungsstellung und Fristen. Auf Basis geltender Gesetze, in vier Sprachen, rund um die Uhr.',
			valuePropsTitle: 'Warum man ihn nutzt',
			valueProps: [
				{
					title: 'Auf Basis geltender Vorschriften',
					body: 'Die Antworten stammen aus den für das laufende Steuerjahr geltenden Gesetzen und Hinweisen — nicht aus dem Allgemeinwissen eines Modells.'
				},
				{
					title: 'Klare Antwort samt Quelle',
					body: 'Zuerst die direkte Antwort, dann die zugrunde liegende Vorschrift — zum Nachprüfen oder zur Weitergabe an Ihre Buchhaltung.'
				},
				{
					title: 'Verfügbar, wenn die Frage aufkommt',
					body: 'Fristen richten sich selten nach Bürozeiten. Der Berater antwortet jederzeit — auf Bulgarisch, Englisch, Deutsch oder Russisch.'
				}
			],
			useCasesTitle: 'Typische Fragen',
			useCases: [
				'Wie hoch sind die Schwellenwerte für die Umsatzsteuerregistrierung in diesem Jahr?',
				'Welche Sozial- und Krankenversicherungsbeiträge zahlen Selbstständige?',
				'Was muss eine ordnungsgemäße Rechnung enthalten, und wann wird eine Gutschrift ausgestellt?',
				'Wann ist die Jahressteuererklärung fällig, und was passiert bei verspäteter Abgabe?',
				'Wie werden Einkünfte aus einem zweiten Arbeitsvertrag besteuert?'
			],
			faqTitle: 'Häufige Fragen',
			faq: [
				{
					q: 'Ist das eine Rechts- oder Steuerberatung?',
					a: 'Nein. Es ist ein Informationsdienst: Er erklärt die Regeln und nennt die zugrunde liegenden Vorschriften. Für eine verbindliche Auskunft zu Ihrem Fall wenden Sie sich an eine zugelassene Steuerberaterin oder einen Steuerberater.'
				},
				{
					q: 'Das Recht welches Landes wird abgedeckt?',
					a: 'Bulgarisches Steuer- und Rechnungslegungsrecht für das laufende Steuerjahr.'
				},
				{
					q: 'In welchen Sprachen kann ich fragen?',
					a: 'Die Oberfläche ist auf Bulgarisch, Englisch, Deutsch und Russisch verfügbar.'
				},
				{
					q: 'Wie aktuell sind die Informationen?',
					a: 'Die Wissensbasis wird anhand der für das laufende Jahr geltenden Gesetze gepflegt; Sätze und Fristen werden als strukturierte Referenzdaten geführt, nicht als Fließtext.'
				}
			],
			ctaTitle: 'Stellen Sie Ihre erste Frage',
			ctaBody: 'Konto erstellen und Gespräch beginnen — ohne Einrichtung, ohne Konfiguration.',
			ctaLabel: 'Gespräch beginnen',
			homeLabel: 'Startseite'
		},
		ru: {
			eyebrow: 'AI-консультант',
			title: 'Налоги и бухгалтерия',
			lead: 'Ставки, пороги, сроки, НДС, счета и бухгалтерский учёт — ответы на основе действующего болгарского налогового и бухгалтерского законодательства, со ссылкой на источник.',
			metaTitle: 'AI-консультант по налогам и бухгалтерии',
			metaDescription:
				'Вопросы по болгарскому НДС, подоходному налогу, взносам, выставлению счетов и срокам подачи. На основе действующего законодательства, на четырёх языках, круглосуточно.',
			valuePropsTitle: 'Почему им пользуются',
			valueProps: [
				{
					title: 'На основе действующих норм',
					body: 'Ответы берутся из законодательства и разъяснений, действующих в текущем налоговом году, а не из общих знаний модели.'
				},
				{
					title: 'Понятный ответ и источник',
					body: 'Сначала прямой ответ, затем норма, на которой он основан — чтобы проверить или передать бухгалтеру.'
				},
				{
					title: 'Доступен, когда возник вопрос',
					body: 'Сроки редко приходятся на рабочие часы. Консультант отвечает в любое время — на болгарском, английском, немецком или русском.'
				}
			],
			useCasesTitle: 'Типичные вопросы',
			useCases: [
				'Каковы пороги регистрации по НДС в этом году и когда регистрация обязательна?',
				'Какие социальные и медицинские взносы платит самозанятое лицо?',
				'Что должен содержать правильно оформленный счёт и когда выставляется кредит-нота?',
				'Когда срок подачи годовой декларации и что будет при опоздании?',
				'Как облагается доход по второму трудовому договору?'
			],
			faqTitle: 'Частые вопросы',
			faq: [
				{
					q: 'Это юридическая или налоговая консультация?',
					a: 'Нет. Это информационный сервис: он объясняет правила и указывает нормы, на которых они основаны. За обязывающим заключением по вашей ситуации обратитесь к лицензированному бухгалтеру или налоговому консультанту.'
				},
				{
					q: 'Законодательство какой страны охватывается?',
					a: 'Болгарское налоговое и бухгалтерское законодательство за текущий налоговый год.'
				},
				{
					q: 'На каких языках можно спрашивать?',
					a: 'Интерфейс доступен на болгарском, английском, немецком и русском.'
				},
				{
					q: 'Насколько актуальна информация?',
					a: 'База знаний поддерживается в соответствии с законодательством текущего года, а ставки и сроки хранятся как структурированные справочные данные, а не как свободный текст.'
				}
			],
			ctaTitle: 'Задайте первый вопрос',
			ctaBody: 'Создайте аккаунт и начните разговор — без настройки и конфигурации.',
			ctaLabel: 'Начать разговор',
			homeLabel: 'Главная'
		}
	}
};
