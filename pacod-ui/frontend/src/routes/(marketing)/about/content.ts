import type { LocalizedDoc } from '$lib/legal/types';
import { COMPANY, legalName, jurisdiction } from '$lib/legal/company';

export const about: LocalizedDoc = {
	en: {
		eyebrow: 'About us',
		title: 'A personal team of AI assistants',
		lead: `${COMPANY.brand} gives everyone access to a panel of specialist AI assistants — the kind of expert help that used to be reserved for large organisations with deep pockets.`,
		metaDescription: `${COMPANY.brand} is your personal team of AI assistants — specialists you can consult on demand.`,
		sections: [
			{
				heading: 'Our mission',
				blocks: [
					{
						k: 'p',
						html: `We believe good advice shouldn't be a luxury. Whether you're looking up a policy, checking a figure, or making sense of a dense document, you deserve a clear, trustworthy answer in plain language. ${COMPANY.brand} exists to make expert reasoning available to anyone, in their own language, at any hour.`
					}
				]
			},
			{
				heading: 'What we do',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} is built around focused assistants, each grounded in a curated knowledge base for its domain. Instead of one generic chatbot, you get specialists that know where to look, cite their sources, and stay within their remit. Behind the scenes, every answer is assembled from vetted reference material rather than guessed from memory.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Specialist assistants</strong> — each tuned to a single subject area.',
							'<strong>Grounded answers</strong> — responses trace back to real source documents.',
							'<strong>Multilingual</strong> — ask in Bulgarian, English, German, or Russian.',
							'<strong>Private by design</strong> — your conversations stay yours.'
						]
					}
				]
			},
			{
				heading: 'How we work',
				blocks: [
					{
						k: 'p',
						html: `We keep our knowledge current, we're transparent about what our assistants can and can't do, and we design every feature so you stay in control of your data. When an assistant isn't confident, it says so — we'd rather give you an honest "I'm not sure" than a confident guess.`
					}
				]
			},
			{
				heading: "Who's behind PACOD",
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} is operated by ${legalName.en}, a company registered in ${jurisdiction.en.country} (ЕИК ${COMPANY.eik}), based in ${jurisdiction.en.city}. We're a small, focused team that cares about accuracy, clarity, and treating your data with respect.`
					}
				]
			},
			{
				heading: 'Get in touch',
				blocks: [
					{
						k: 'p',
						html: `Questions, partnership ideas, or feedback? We'd love to hear from you — head over to our <a href="/contact">contact page</a>.`
					}
				]
			}
		]
	},

	bg: {
		eyebrow: 'За нас',
		title: 'Личен екип от AI асистенти',
		lead: `${COMPANY.brand} осигурява на всеки достъп до екип от специализирани AI асистенти — експертната помощ, която досега беше запазена за големи организации с дълбоки джобове.`,
		metaDescription: `${COMPANY.brand} е Вашият личен екип от AI асистенти — специалисти, с които можете да се консултирате при поискване.`,
		sections: [
			{
				heading: 'Нашата мисия',
				blocks: [
					{
						k: 'p',
						html: `Вярваме, че добрият съвет не бива да е лукс. Независимо дали проверявате правило, търсите точна стойност или се опитвате да разчетете сложен документ, заслужавате ясен и надежден отговор на достъпен език. ${COMPANY.brand} съществува, за да направи експертните разсъждения достъпни за всеки, на неговия собствен език, по всяко време.`
					}
				]
			},
			{
				heading: 'Какво правим',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} е изграден около специализирани асистенти, всеки от които стъпва върху подбрана база от знания за своята област. Вместо един универсален чатбот получавате специалисти, които знаят къде да търсят, посочват източниците си и се придържат към своята компетентност. Зад кулисите всеки отговор се съставя от проверени справочни материали, а не се предполага по памет.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Специализирани асистенти</strong> — всеки настроен за една предметна област.',
							'<strong>Обосновани отговори</strong> — отговорите се проследяват до реални изходни документи.',
							'<strong>Многоезичност</strong> — питайте на български, английски, немски или руски.',
							'<strong>Поверителност по замисъл</strong> — Вашите разговори остават Ваши.'
						]
					}
				]
			},
			{
				heading: 'Как работим',
				blocks: [
					{
						k: 'p',
						html: `Поддържаме знанията си актуални, прозрачни сме относно това какво могат и какво не могат нашите асистенти и проектираме всяка функция така, че Вие да запазвате контрол над данните си. Когато консултантът не е сигурен, той го заявява — предпочитаме да Ви дадем честно „Не съм сигурен“, вместо уверено предположение.`
					}
				]
			},
			{
				heading: 'Кой стои зад PACOD',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} се управлява от ${legalName.bg}, дружество, регистрирано в ${jurisdiction.bg.country} (ЕИК ${COMPANY.eik}), със седалище в ${jurisdiction.bg.city}. Ние сме малък, съсредоточен екип, за когото са важни точността, яснотата и уважителното отношение към Вашите данни.`
					}
				]
			},
			{
				heading: 'Свържете се с нас',
				blocks: [
					{
						k: 'p',
						html: `Въпроси, идеи за партньорство или обратна връзка? С удоволствие ще Ви изслушаме — посетете нашата <a href="/contact">страница за контакт</a>.`
					}
				]
			}
		]
	},

	de: {
		eyebrow: 'Über uns',
		title: 'Ein persönliches Team von KI-Beratern',
		lead: `${COMPANY.brand} verschafft jedem Zugang zu einem Kreis spezialisierter KI-Berater — der Art von Expertenhilfe, die bislang großen Organisationen mit tiefen Taschen vorbehalten war.`,
		metaDescription: `${COMPANY.brand} ist Ihr persönliches Team von KI-Beratern — Fachleute, die Sie bei Bedarf zurate ziehen können.`,
		sections: [
			{
				heading: 'Unsere Mission',
				blocks: [
					{
						k: 'p',
						html: `Wir sind überzeugt, dass guter Rat kein Luxus sein sollte. Ob Sie eine Richtlinie nachschlagen, eine Zahl prüfen oder ein komplexes Dokument verstehen wollen — Sie verdienen eine klare, vertrauenswürdige Antwort in verständlicher Sprache. ${COMPANY.brand} gibt es, um fundierte Sachkenntnis für jeden verfügbar zu machen, in seiner eigenen Sprache und zu jeder Stunde.`
					}
				]
			},
			{
				heading: 'Was wir tun',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} ist um spezialisierte Berater herum aufgebaut, die jeweils auf einer kuratierten Wissensbasis für ihr Fachgebiet beruhen. Statt eines allgemeinen Chatbots erhalten Sie Fachleute, die wissen, wo sie nachschlagen müssen, ihre Quellen angeben und in ihrem Zuständigkeitsbereich bleiben. Im Hintergrund wird jede Antwort aus geprüftem Referenzmaterial zusammengestellt und nicht aus dem Gedächtnis geraten.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Spezialisierte Berater</strong> — jeder auf ein einzelnes Fachgebiet abgestimmt.',
							'<strong>Fundierte Antworten</strong> — Antworten lassen sich auf reale Quelldokumente zurückführen.',
							'<strong>Mehrsprachig</strong> — fragen Sie auf Bulgarisch, Englisch, Deutsch oder Russisch.',
							'<strong>Vertraulich by design</strong> — Ihre Gespräche bleiben Ihre.'
						]
					}
				]
			},
			{
				heading: 'Wie wir arbeiten',
				blocks: [
					{
						k: 'p',
						html: `Wir halten unser Wissen aktuell, wir sind transparent darüber, was unsere Berater können und was nicht, und wir gestalten jede Funktion so, dass Sie die Kontrolle über Ihre Daten behalten. Wenn ein Berater unsicher ist, sagt er das — wir geben Ihnen lieber ein ehrliches „Ich bin nicht sicher“ als eine selbstbewusste Vermutung.`
					}
				]
			},
			{
				heading: 'Wer hinter PACOD steht',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} wird betrieben von ${legalName.de}, einem in ${jurisdiction.de.country} eingetragenen Unternehmen (ЕИК (UIC) ${COMPANY.eik}), mit Sitz in ${jurisdiction.de.city}. Wir sind ein kleines, fokussiertes Team, dem Genauigkeit, Klarheit und ein respektvoller Umgang mit Ihren Daten am Herzen liegen.`
					}
				]
			},
			{
				heading: 'Kontakt aufnehmen',
				blocks: [
					{
						k: 'p',
						html: `Fragen, Ideen für eine Partnerschaft oder Rückmeldungen? Wir freuen uns, von Ihnen zu hören — besuchen Sie unsere <a href="/contact">Kontaktseite</a>.`
					}
				]
			}
		]
	},

	ru: {
		eyebrow: 'О нас',
		title: 'Личная команда AI-консультантов',
		lead: `${COMPANY.brand} открывает каждому доступ к группе специализированных AI-консультантов — той экспертной помощи, которая прежде была доступна лишь крупным организациям с большими бюджетами.`,
		metaDescription: `${COMPANY.brand} — это Ваша личная команда AI-консультантов, специалистов, к которым можно обратиться в любой момент.`,
		sections: [
			{
				heading: 'Наша миссия',
				blocks: [
					{
						k: 'p',
						html: `Мы убеждены, что хороший совет не должен быть роскошью. Ищете ли Вы правило, проверяете ли точное значение или пытаетесь разобраться в сложном документе — Вы заслуживаете ясного и надёжного ответа на понятном языке. ${COMPANY.brand} существует для того, чтобы сделать экспертные рассуждения доступными каждому, на его родном языке и в любое время.`
					}
				]
			},
			{
				heading: 'Чем мы занимаемся',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} построен вокруг специализированных консультантов, каждый из которых опирается на тщательно подобранную базу знаний в своей области. Вместо одного универсального чат-бота Вы получаете специалистов, которые знают, где искать, указывают свои источники и остаются в рамках своей компетенции. За кулисами каждый ответ собирается из проверенных справочных материалов, а не додумывается по памяти.`
					},
					{
						k: 'ul',
						items: [
							'<strong>Специализированные консультанты</strong> — каждый настроен на одну предметную область.',
							'<strong>Обоснованные ответы</strong> — ответы прослеживаются до реальных исходных документов.',
							'<strong>Многоязычность</strong> — задавайте вопросы на болгарском, английском, немецком или русском.',
							'<strong>Конфиденциальность по замыслу</strong> — Ваши разговоры остаются Вашими.'
						]
					}
				]
			},
			{
				heading: 'Как мы работаем',
				blocks: [
					{
						k: 'p',
						html: `Мы поддерживаем свои знания в актуальном состоянии, открыто говорим о том, что наши консультанты могут, а чего не могут, и проектируем каждую функцию так, чтобы Вы сохраняли контроль над своими данными. Когда консультант не уверен, он прямо об этом говорит — мы предпочитаем дать честное «Я не уверен», чем уверенную догадку.`
					}
				]
			},
			{
				heading: 'Кто стоит за PACOD',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} управляется компанией ${legalName.ru}, зарегистрированной в ${jurisdiction.ru.country} (ЕИК (UIC) ${COMPANY.eik}), с местонахождением в городе ${jurisdiction.ru.city}. Мы — небольшая, сосредоточенная команда, для которой важны точность, ясность и бережное отношение к Вашим данным.`
					}
				]
			},
			{
				heading: 'Связаться с нами',
				blocks: [
					{
						k: 'p',
						html: `Вопросы, идеи для сотрудничества или отзывы? Мы будем рады Вам — перейдите на нашу <a href="/contact">страницу контактов</a>.`
					}
				]
			}
		]
	}
};
