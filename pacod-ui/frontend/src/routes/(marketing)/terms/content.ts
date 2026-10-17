import type { LocalizedDoc } from '$lib/legal/types';
import { COMPANY, legalName, jurisdiction, lastUpdated } from '$lib/legal/company';

const email = COMPANY.email;

export const terms: LocalizedDoc = {
	en: {
		eyebrow: 'Legal',
		title: 'Terms of Service',
		lead: `These terms are the agreement between you and ${legalName.en} for your use of ${COMPANY.brand}. Please read them carefully.`,
		metaDescription: `The terms that govern your use of ${COMPANY.brand}.`,
		updatedLine: `Last updated: ${lastUpdated.terms.en} · Effective: ${lastUpdated.terms.en}`,
		sections: [
			{
				heading: '1. Acceptance of terms',
				blocks: [
					{
						k: 'p',
						html: `By creating an account or using ${COMPANY.brand} (the "Service"), you agree to be bound by these Terms. If you do not agree, please do not use the Service. If you use the Service on behalf of an organisation, you confirm you're authorised to accept these Terms for it.`
					}
				]
			},
			{
				heading: '2. The Service',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} provides AI-powered assistant tools that generate responses from a curated knowledge base. <strong>The Service is an informational aid, not a substitute for professional advice.</strong> Responses may be incomplete or inaccurate, and you are responsible for verifying anything you rely on — particularly for legal, tax, financial, or accounting decisions.`
					}
				]
			},
			{
				heading: '3. Your account',
				blocks: [
					{
						k: 'ul',
						items: [
							'You must provide accurate registration details and keep them up to date.',
							'You are responsible for keeping your credentials secure and for activity under your account.',
							'You must be at least 18 years old, or the age of legal majority in your country.',
							'Notify us promptly of any unauthorised use.'
						]
					}
				]
			},
			{
				heading: '4. Acceptable use',
				blocks: [
					{ k: 'p', html: 'You agree not to:' },
					{
						k: 'ul',
						items: [
							'Use the Service for any unlawful purpose or in breach of any regulation;',
							'Attempt to reverse-engineer, disrupt, or gain unauthorised access to the Service;',
							"Submit content that infringes others' rights or that you have no right to share;",
							'Overload the Service with automated requests beyond permitted limits;',
							'Use outputs in a way that misrepresents them as professional or certified advice.'
						]
					}
				]
			},
			{
				heading: '5. Your content',
				blocks: [
					{
						k: 'p',
						html: 'You retain ownership of the content you submit. You grant us a limited licence to process it solely to operate and improve the Service, as described in our <a href="/privacy">Privacy Policy</a>. You are responsible for the legality of the content you provide.'
					}
				]
			},
			{
				heading: '6. Intellectual property',
				blocks: [
					{
						k: 'p',
						html: `The Service, including its software, design, and branding, is owned by ${legalName.en} and protected by intellectual-property laws. These Terms grant you a personal, non-exclusive, non-transferable right to use the Service — nothing more.`
					}
				]
			},
			{
				heading: '7. Fees and plans',
				blocks: [
					{
						k: 'p',
						html: 'The Service is currently provided <strong>free of charge</strong>. We may introduce paid plans in the future; if we do, we will publish the pricing, billing cycle, and any applicable taxes in advance, and paid features will never be activated without your explicit agreement. Any free allowances or usage limits in effect at the time apply.'
					}
				]
			},
			{
				heading: '8. Disclaimers',
				blocks: [
					{
						k: 'p',
						html: 'The Service is provided <strong>"as is" and "as available"</strong>, without warranties of any kind, whether express or implied, including fitness for a particular purpose, accuracy, or non-infringement. We do not warrant that the Service will be uninterrupted, error-free, or that AI-generated outputs will be correct.'
					}
				]
			},
			{
				heading: '9. Limitation of liability',
				blocks: [
					{
						k: 'p',
						html: `To the fullest extent permitted by law, ${legalName.en} shall not be liable for any indirect, incidental, or consequential damages, or for any loss arising from your reliance on AI-generated outputs. Because the Service is currently provided free of charge, our aggregate liability for any claim is limited to the amount you have paid us for the Service in the twelve months preceding the claim. Nothing in these Terms excludes liability that cannot be excluded under applicable law.`
					}
				]
			},
			{
				heading: '10. Termination',
				blocks: [
					{
						k: 'p',
						html: `You may stop using the Service and close your account at any time by emailing <a href="mailto:${email}">${email}</a> from the address registered to it; we then delete your data as described in our <a href="/privacy">Privacy Policy</a>. We may suspend or terminate your access if you breach these Terms or where required by law. On termination, the provisions that by their nature should survive (e.g. IP, disclaimers, liability) continue to apply.`
					}
				]
			},
			{
				heading: '11. Governing law',
				blocks: [
					{
						k: 'p',
						html: `These Terms are governed by the laws of ${jurisdiction.en.country}, without regard to conflict-of-law rules, and the courts of ${jurisdiction.en.city}, ${jurisdiction.en.country} have exclusive jurisdiction, subject to any mandatory consumer-protection rights you have under EU law.`
					}
				]
			},
			{
				heading: '12. Changes to these terms',
				blocks: [
					{
						k: 'p',
						html: 'We may update these Terms from time to time. We\'ll post the updated version here and revise the "Last updated" date; material changes will be notified where appropriate. Continued use after changes take effect means you accept the revised Terms.'
					}
				]
			},
			{
				heading: '13. Contact',
				blocks: [
					{
						k: 'p',
						html: `Questions about these Terms? Reach us via our <a href="/contact">contact page</a> or at <a href="mailto:${email}">${email}</a>. The Service is operated by ${legalName.en}, ЕИК ${COMPANY.eik}, ${jurisdiction.en.city}, ${jurisdiction.en.country}.`
					}
				]
			}
		]
	},

	bg: {
		eyebrow: 'Правна информация',
		title: 'Общи условия',
		lead: `Тези условия представляват споразумението между Вас и ${legalName.bg} за използването на ${COMPANY.brand}. Моля, прочетете ги внимателно.`,
		metaDescription: `Условията, които уреждат използването на ${COMPANY.brand}.`,
		updatedLine: `Последна редакция: ${lastUpdated.terms.bg} · В сила от: ${lastUpdated.terms.bg}`,
		sections: [
			{
				heading: '1. Приемане на условията',
				blocks: [
					{
						k: 'p',
						html: `Със създаването на профил или използването на ${COMPANY.brand} („Услугата“) Вие се съгласявате да бъдете обвързани с настоящите Условия. Ако не сте съгласни, моля, не използвайте Услугата. Ако използвате Услугата от името на организация, Вие потвърждавате, че сте оправомощени да приемете тези Условия от нейно име.`
					}
				]
			},
			{
				heading: '2. Услугата',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} предоставя консултантски инструменти с изкуствен интелект, които генерират отговори въз основа на подбрана база от знания. <strong>Услугата е информационно помощно средство, а не заместител на професионална консултация.</strong> Отговорите може да са непълни или неточни и Вие носите отговорност да проверите всичко, на което разчитате — особено при правни, данъчни, финансови или счетоводни решения.`
					}
				]
			},
			{
				heading: '3. Вашият профил',
				blocks: [
					{
						k: 'ul',
						items: [
							'Трябва да предоставите точни данни при регистрация и да ги поддържате актуални.',
							'Вие отговаряте за опазването на своите идентификационни данни и за действията, извършени чрез Вашия профил.',
							'Трябва да сте навършили поне 18 години или възрастта за пълнолетие във Вашата държава.',
							'Уведомете ни своевременно за всяко неоторизирано използване.'
						]
					}
				]
			},
			{
				heading: '4. Допустимо използване',
				blocks: [
					{ k: 'p', html: 'Вие се съгласявате да не:' },
					{
						k: 'ul',
						items: [
							'използвате Услугата за незаконни цели или в нарушение на нормативната уредба;',
							'се опитвате да декомпилирате, нарушавате работата или получавате неоторизиран достъп до Услугата;',
							'подавате съдържание, което нарушава правата на трети лица или което нямате право да споделяте;',
							'претоварвате Услугата с автоматизирани заявки над разрешените лимити;',
							'използвате резултатите по начин, който ги представя невярно като професионална или сертифицирана консултация.'
						]
					}
				]
			},
			{
				heading: '5. Вашето съдържание',
				blocks: [
					{
						k: 'p',
						html: 'Вие запазвате собствеността върху съдържанието, което подавате. Предоставяте ни ограничен лиценз да го обработваме единствено с цел предоставяне и подобряване на Услугата, както е описано в нашата <a href="/privacy">Политика за поверителност</a>. Вие носите отговорност за законосъобразността на съдържанието, което предоставяте.'
					}
				]
			},
			{
				heading: '6. Интелектуална собственост',
				blocks: [
					{
						k: 'p',
						html: `Услугата, включително нейният софтуер, дизайн и марка, е собственост на ${legalName.bg} и е защитена от законите за интелектуална собственост. Настоящите Условия Ви предоставят лично, неизключително и непрехвърляемо право да използвате Услугата — нищо повече.`
					}
				]
			},
			{
				heading: '7. Такси и планове',
				blocks: [
					{
						k: 'p',
						html: 'Понастоящем Услугата се предоставя <strong>безплатно</strong>. Може да въведем платени планове в бъдеще; ако го направим, ще публикуваме предварително цените, периода на таксуване и приложимите данъци, като платените функции никога няма да бъдат активирани без Вашето изрично съгласие. Прилагат се безплатните лимити или ограничения за използване, действащи към съответния момент.'
					}
				]
			},
			{
				heading: '8. Отказ от отговорност',
				blocks: [
					{
						k: 'p',
						html: 'Услугата се предоставя <strong>„във вида, в който е“ и „според наличността“</strong>, без каквито и да е гаранции, изрични или подразбиращи се, включително за годност за определена цел, точност или ненарушаване на права. Не гарантираме, че Услугата ще бъде непрекъсната, без грешки или че генерираните от изкуствен интелект резултати ще бъдат верни.'
					}
				]
			},
			{
				heading: '9. Ограничаване на отговорността',
				blocks: [
					{
						k: 'p',
						html: `В максималната допустима от закона степен ${legalName.bg} не носи отговорност за каквито и да е косвени, случайни или последващи вреди, нито за загуби, произтичащи от Вашето доверие в генерираните от изкуствен интелект резултати. Тъй като понастоящем Услугата се предоставя безплатно, общата ни отговорност за всяка претенция е ограничена до сумата, която сте ни платили за Услугата през дванадесетте месеца, предхождащи претенцията. Нищо в настоящите Условия не изключва отговорност, която не може да бъде изключена съгласно приложимото право.`
					}
				]
			},
			{
				heading: '10. Прекратяване',
				blocks: [
					{
						k: 'p',
						html: `Можете да преустановите използването на Услугата и да закриете профила си по всяко време, като пишете на <a href="mailto:${email}">${email}</a> от адреса, регистриран за него; след това изтриваме данните Ви, както е описано в нашата <a href="/privacy">Политика за поверителност</a>. Можем да спрем или прекратим достъпа Ви, ако нарушите настоящите Условия или когато това се изисква от закона. При прекратяване продължават да се прилагат разпоредбите, които по своето естество следва да останат в сила (напр. интелектуална собственост, откази от отговорност, отговорност).`
					}
				]
			},
			{
				heading: '11. Приложимо право',
				blocks: [
					{
						k: 'p',
						html: `Настоящите Условия се уреждат от законите на ${jurisdiction.bg.country}, без оглед на стълкновителните норми, като съдилищата в ${jurisdiction.bg.city}, ${jurisdiction.bg.country} имат изключителна компетентност, при спазване на всички императивни права за защита на потребителите, които имате съгласно правото на ЕС.`
					}
				]
			},
			{
				heading: '12. Промени в настоящите условия',
				blocks: [
					{
						k: 'p',
						html: 'Може периодично да актуализираме настоящите Условия. Ще публикуваме актуализираната версия тук и ще променим датата „Последна редакция“; за съществени промени ще бъдете уведомявани по подходящ начин. Продължаването на използването след влизането на промените в сила означава, че приемате изменените Условия.'
					}
				]
			},
			{
				heading: '13. Контакт',
				blocks: [
					{
						k: 'p',
						html: `Въпроси относно настоящите Условия? Свържете се с нас чрез нашата <a href="/contact">страница за контакт</a> или на <a href="mailto:${email}">${email}</a>. Услугата се предоставя от ${legalName.bg}, ЕИК ${COMPANY.eik}, ${jurisdiction.bg.city}, ${jurisdiction.bg.country}.`
					}
				]
			}
		]
	},

	de: {
		eyebrow: 'Rechtliches',
		title: 'Nutzungsbedingungen',
		lead: `Diese Bedingungen bilden die Vereinbarung zwischen Ihnen und ${legalName.de} über Ihre Nutzung von ${COMPANY.brand}. Bitte lesen Sie sie sorgfältig.`,
		metaDescription: `Die Bedingungen, die Ihre Nutzung von ${COMPANY.brand} regeln.`,
		updatedLine: `Zuletzt aktualisiert: ${lastUpdated.terms.de} · Gültig ab: ${lastUpdated.terms.de}`,
		sections: [
			{
				heading: '1. Annahme der Bedingungen',
				blocks: [
					{
						k: 'p',
						html: `Indem Sie ein Konto erstellen oder ${COMPANY.brand} (den „Dienst“) nutzen, erklären Sie sich mit diesen Bedingungen einverstanden. Wenn Sie nicht einverstanden sind, nutzen Sie den Dienst bitte nicht. Wenn Sie den Dienst im Namen einer Organisation nutzen, bestätigen Sie, dass Sie befugt sind, diese Bedingungen in deren Namen anzunehmen.`
					}
				]
			},
			{
				heading: '2. Der Dienst',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} stellt KI-gestützte Beratungswerkzeuge bereit, die Antworten aus einer kuratierten Wissensbasis erzeugen. <strong>Der Dienst ist eine informative Hilfe und kein Ersatz für professionelle Beratung.</strong> Antworten können unvollständig oder ungenau sein, und Sie sind dafür verantwortlich, alles zu überprüfen, worauf Sie sich verlassen — insbesondere bei rechtlichen, steuerlichen, finanziellen oder buchhalterischen Entscheidungen.`
					}
				]
			},
			{
				heading: '3. Ihr Konto',
				blocks: [
					{
						k: 'ul',
						items: [
							'Sie müssen bei der Registrierung korrekte Angaben machen und diese aktuell halten.',
							'Sie sind für die Sicherheit Ihrer Zugangsdaten und für Aktivitäten unter Ihrem Konto verantwortlich.',
							'Sie müssen mindestens 18 Jahre alt sein oder das Volljährigkeitsalter in Ihrem Land erreicht haben.',
							'Benachrichtigen Sie uns unverzüglich über jede unbefugte Nutzung.'
						]
					}
				]
			},
			{
				heading: '4. Zulässige Nutzung',
				blocks: [
					{ k: 'p', html: 'Sie verpflichten sich, Folgendes zu unterlassen:' },
					{
						k: 'ul',
						items: [
							'den Dienst für rechtswidrige Zwecke oder unter Verstoß gegen Vorschriften zu nutzen;',
							'zu versuchen, den Dienst zurückzuentwickeln, zu stören oder unbefugten Zugang zu erlangen;',
							'Inhalte einzureichen, die Rechte Dritter verletzen oder zu deren Weitergabe Sie nicht berechtigt sind;',
							'den Dienst mit automatisierten Anfragen über die zulässigen Grenzen hinaus zu überlasten;',
							'Ergebnisse so zu verwenden, dass sie fälschlich als professionelle oder zertifizierte Beratung dargestellt werden.'
						]
					}
				]
			},
			{
				heading: '5. Ihre Inhalte',
				blocks: [
					{
						k: 'p',
						html: 'Sie behalten das Eigentum an den von Ihnen eingereichten Inhalten. Sie gewähren uns eine beschränkte Lizenz, diese ausschließlich zum Betrieb und zur Verbesserung des Dienstes zu verarbeiten, wie in unserer <a href="/privacy">Datenschutzerklärung</a> beschrieben. Sie sind für die Rechtmäßigkeit der von Ihnen bereitgestellten Inhalte verantwortlich.'
					}
				]
			},
			{
				heading: '6. Geistiges Eigentum',
				blocks: [
					{
						k: 'p',
						html: `Der Dienst, einschließlich seiner Software, seines Designs und seiner Marke, ist Eigentum von ${legalName.de} und durch Gesetze zum Schutz des geistigen Eigentums geschützt. Diese Bedingungen gewähren Ihnen ein persönliches, nicht ausschließliches und nicht übertragbares Recht zur Nutzung des Dienstes — nicht mehr.`
					}
				]
			},
			{
				heading: '7. Entgelte und Tarife',
				blocks: [
					{
						k: 'p',
						html: 'Der Dienst wird derzeit <strong>kostenlos</strong> bereitgestellt. Wir können künftig kostenpflichtige Tarife einführen; in diesem Fall veröffentlichen wir die Preise, den Abrechnungszeitraum und etwaige Steuern im Voraus, und kostenpflichtige Funktionen werden niemals ohne Ihre ausdrückliche Zustimmung aktiviert. Es gelten die jeweils geltenden kostenlosen Kontingente oder Nutzungsgrenzen.'
					}
				]
			},
			{
				heading: '8. Haftungsausschlüsse',
				blocks: [
					{
						k: 'p',
						html: 'Der Dienst wird <strong>„wie besehen“ und „wie verfügbar“</strong> bereitgestellt, ohne jegliche ausdrückliche oder stillschweigende Gewährleistung, einschließlich der Eignung für einen bestimmten Zweck, der Richtigkeit oder der Nichtverletzung von Rechten. Wir gewährleisten nicht, dass der Dienst unterbrechungsfrei oder fehlerfrei ist oder dass KI-generierte Ergebnisse zutreffend sind.'
					}
				]
			},
			{
				heading: '9. Haftungsbeschränkung',
				blocks: [
					{
						k: 'p',
						html: `Soweit gesetzlich zulässig, haftet ${legalName.de} nicht für indirekte Schäden, Neben- oder Folgeschäden oder für Verluste, die sich aus Ihrem Vertrauen auf KI-generierte Ergebnisse ergeben. Da der Dienst derzeit kostenlos bereitgestellt wird, ist unsere Gesamthaftung für jeden Anspruch auf den Betrag begrenzt, den Sie in den zwölf Monaten vor dem Anspruch für den Dienst an uns gezahlt haben. Diese Bedingungen schließen keine Haftung aus, die nach geltendem Recht nicht ausgeschlossen werden kann.`
					}
				]
			},
			{
				heading: '10. Kündigung',
				blocks: [
					{
						k: 'p',
						html: `Sie können die Nutzung des Dienstes jederzeit einstellen und Ihr Konto schließen, indem Sie von der dafür registrierten Adresse an <a href="mailto:${email}">${email}</a> schreiben; wir löschen Ihre Daten dann wie in unserer <a href="/privacy">Datenschutzerklärung</a> beschrieben. Wir können Ihren Zugang aussetzen oder beenden, wenn Sie gegen diese Bedingungen verstoßen oder soweit gesetzlich erforderlich. Nach Beendigung gelten die Bestimmungen weiter, die ihrer Natur nach fortbestehen sollen (z. B. geistiges Eigentum, Haftungsausschlüsse, Haftung).`
					}
				]
			},
			{
				heading: '11. Anwendbares Recht',
				blocks: [
					{
						k: 'p',
						html: `Diese Bedingungen unterliegen dem Recht ${jurisdiction.de.country === 'Bulgarien' ? 'Bulgariens' : jurisdiction.de.country}, ohne Rücksicht auf Kollisionsnormen, und die Gerichte in ${jurisdiction.de.city}, ${jurisdiction.de.country} sind ausschließlich zuständig, vorbehaltlich zwingender Verbraucherschutzrechte, die Ihnen nach EU-Recht zustehen.`
					}
				]
			},
			{
				heading: '12. Änderungen dieser Bedingungen',
				blocks: [
					{
						k: 'p',
						html: 'Wir können diese Bedingungen von Zeit zu Zeit aktualisieren. Wir veröffentlichen die aktualisierte Fassung hier und passen das Datum „Zuletzt aktualisiert“ an; über wesentliche Änderungen informieren wir gegebenenfalls gesondert. Die weitere Nutzung nach Inkrafttreten der Änderungen gilt als Annahme der geänderten Bedingungen.'
					}
				]
			},
			{
				heading: '13. Kontakt',
				blocks: [
					{
						k: 'p',
						html: `Fragen zu diesen Bedingungen? Erreichen Sie uns über unsere <a href="/contact">Kontaktseite</a> oder unter <a href="mailto:${email}">${email}</a>. Der Dienst wird betrieben von ${legalName.de}, ЕИК (UIC) ${COMPANY.eik}, ${jurisdiction.de.city}, ${jurisdiction.de.country}.`
					}
				]
			}
		]
	},

	ru: {
		eyebrow: 'Правовая информация',
		title: 'Условия использования',
		lead: `Настоящие условия представляют собой соглашение между Вами и ${legalName.ru} об использовании ${COMPANY.brand}. Пожалуйста, внимательно ознакомьтесь с ними.`,
		metaDescription: `Условия, регулирующие использование ${COMPANY.brand}.`,
		updatedLine: `Последнее обновление: ${lastUpdated.terms.ru} · Вступает в силу: ${lastUpdated.terms.ru}`,
		sections: [
			{
				heading: '1. Принятие условий',
				blocks: [
					{
						k: 'p',
						html: `Создавая учётную запись или используя ${COMPANY.brand} («Сервис»), Вы соглашаетесь соблюдать настоящие Условия. Если Вы не согласны, пожалуйста, не используйте Сервис. Если Вы используете Сервис от имени организации, Вы подтверждаете, что уполномочены принять настоящие Условия от её имени.`
					}
				]
			},
			{
				heading: '2. Сервис',
				blocks: [
					{
						k: 'p',
						html: `${COMPANY.brand} предоставляет консультационные инструменты на базе искусственного интеллекта, которые формируют ответы на основе тщательно подобранной базы знаний. <strong>Сервис является информационным вспомогательным средством, а не заменой профессиональной консультации.</strong> Ответы могут быть неполными или неточными, и Вы несёте ответственность за проверку всего, на что полагаетесь, — особенно при принятии юридических, налоговых, финансовых или бухгалтерских решений.`
					}
				]
			},
			{
				heading: '3. Ваша учётная запись',
				blocks: [
					{
						k: 'ul',
						items: [
							'Вы обязаны предоставлять достоверные регистрационные данные и поддерживать их актуальность.',
							'Вы несёте ответственность за сохранность своих учётных данных и за действия, совершённые под Вашей учётной записью.',
							'Вам должно быть не менее 18 лет либо Вы должны достичь возраста совершеннолетия в Вашей стране.',
							'Незамедлительно уведомляйте нас о любом несанкционированном использовании.'
						]
					}
				]
			},
			{
				heading: '4. Допустимое использование',
				blocks: [
					{ k: 'p', html: 'Вы обязуетесь не:' },
					{
						k: 'ul',
						items: [
							'использовать Сервис в противоправных целях или в нарушение каких-либо нормативных требований;',
							'пытаться выполнять обратное проектирование, нарушать работу или получать несанкционированный доступ к Сервису;',
							'размещать материалы, нарушающие права третьих лиц или на распространение которых у Вас нет прав;',
							'перегружать Сервис автоматизированными запросами сверх допустимых лимитов;',
							'использовать результаты таким образом, чтобы выдавать их за профессиональную или сертифицированную консультацию.'
						]
					}
				]
			},
			{
				heading: '5. Ваш контент',
				blocks: [
					{
						k: 'p',
						html: 'Вы сохраняете право собственности на предоставляемый Вами контент. Вы предоставляете нам ограниченную лицензию на его обработку исключительно в целях работы и улучшения Сервиса, как описано в нашей <a href="/privacy">Политике конфиденциальности</a>. Вы несёте ответственность за законность предоставляемого Вами контента.'
					}
				]
			},
			{
				heading: '6. Интеллектуальная собственность',
				blocks: [
					{
						k: 'p',
						html: `Сервис, включая его программное обеспечение, дизайн и товарный знак, принадлежит ${legalName.ru} и защищён законами об интеллектуальной собственности. Настоящие Условия предоставляют Вам личное, неисключительное и непередаваемое право на использование Сервиса — и не более того.`
					}
				]
			},
			{
				heading: '7. Плата и тарифы',
				blocks: [
					{
						k: 'p',
						html: 'В настоящее время Сервис предоставляется <strong>бесплатно</strong>. В будущем мы можем ввести платные тарифы; в этом случае мы заранее опубликуем цены, период оплаты и применимые налоги, а платные функции никогда не будут активированы без Вашего явного согласия. Применяются действующие на соответствующий момент бесплатные лимиты или ограничения использования.'
					}
				]
			},
			{
				heading: '8. Отказ от гарантий',
				blocks: [
					{
						k: 'p',
						html: 'Сервис предоставляется <strong>«как есть» и «по мере доступности»</strong>, без каких-либо гарантий, явных или подразумеваемых, включая пригодность для определённой цели, точность или ненарушение прав. Мы не гарантируем, что работа Сервиса будет бесперебойной, безошибочной или что результаты, сгенерированные искусственным интеллектом, будут верными.'
					}
				]
			},
			{
				heading: '9. Ограничение ответственности',
				blocks: [
					{
						k: 'p',
						html: `В максимально допустимой законом степени ${legalName.ru} не несёт ответственности за какие-либо косвенные, случайные или последующие убытки, а также за любые потери, возникшие вследствие Вашего доверия к результатам, сгенерированным искусственным интеллектом. Поскольку в настоящее время Сервис предоставляется бесплатно, наша совокупная ответственность по любому требованию ограничена суммой, которую Вы уплатили нам за Сервис за двенадцать месяцев, предшествующих требованию. Ничто в настоящих Условиях не исключает ответственность, которая не может быть исключена согласно применимому праву.`
					}
				]
			},
			{
				heading: '10. Прекращение',
				blocks: [
					{
						k: 'p',
						html: `Вы можете прекратить использование Сервиса и закрыть свою учётную запись в любое время, написав на <a href="mailto:${email}">${email}</a> с зарегистрированного в ней адреса; после этого мы удалим Ваши данные, как описано в нашей <a href="/privacy">Политике конфиденциальности</a>. Мы можем приостановить или прекратить Ваш доступ в случае нарушения Вами настоящих Условий или когда это требуется по закону. После прекращения продолжают действовать положения, которые по своей природе должны сохранять силу (например, интеллектуальная собственность, отказ от гарантий, ответственность).`
					}
				]
			},
			{
				heading: '11. Применимое право',
				blocks: [
					{
						k: 'p',
						html: `Настоящие Условия регулируются законодательством Республики ${jurisdiction.ru.country}, без учёта коллизионных норм, и суды города ${jurisdiction.ru.city}, ${jurisdiction.ru.country}, обладают исключительной юрисдикцией, при соблюдении любых императивных прав по защите прав потребителей, которыми Вы обладаете согласно праву ЕС.`
					}
				]
			},
			{
				heading: '12. Изменения настоящих условий',
				blocks: [
					{
						k: 'p',
						html: 'Мы можем время от времени обновлять настоящие Условия. Мы разместим обновлённую версию здесь и изменим дату «Последнее обновление»; о существенных изменениях мы уведомим надлежащим образом. Продолжение использования после вступления изменений в силу означает Ваше согласие с изменёнными Условиями.'
					}
				]
			},
			{
				heading: '13. Контакты',
				blocks: [
					{
						k: 'p',
						html: `Вопросы по настоящим Условиям? Свяжитесь с нами через нашу <a href="/contact">страницу контактов</a> или по адресу <a href="mailto:${email}">${email}</a>. Сервис управляется ${legalName.ru}, ЕИК (UIC) ${COMPANY.eik}, ${jurisdiction.ru.city}, ${jurisdiction.ru.country}.`
					}
				]
			}
		]
	}
};
