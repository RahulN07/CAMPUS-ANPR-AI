from django.core.management.base import BaseCommand

from vehicles.models import VehicleCompany, VehicleModel


VEHICLE_DATA = {
    "TWO_WHEELER": {
        "Honda": [
            "Activa 6G",
            "Activa 125",
            "Dio",
            "Shine 100",
            "Shine 125",
            "SP 125",
            "Unicorn",
            "Hornet 2.0",
            "CB200X",
            "Hness CB350",
            "CB350RS",
        ],
        "Hero": [
            "Splendor Plus",
            "HF Deluxe",
            "Passion Plus",
            "Glamour",
            "Super Splendor",
            "Xtreme 125R",
            "Xtreme 160R",
            "Xpulse 200",
            "Pleasure Plus",
            "Destini 125",
        ],
        "TVS": [
            "Jupiter",
            "Jupiter 125",
            "Ntorq 125",
            "Raider 125",
            "Radeon",
            "Sport",
            "Apache RTR 160",
            "Apache RTR 160 4V",
            "Apache RTR 200 4V",
            "Apache RR 310",
            "iQube",
        ],
        "Bajaj": [
            "Pulsar 125",
            "Pulsar 150",
            "Pulsar N160",
            "Pulsar NS200",
            "Pulsar RS200",
            "Avenger 160",
            "Avenger 220",
            "Dominar 250",
            "Dominar 400",
            "Chetak",
            "Platina 100",
            "CT 110X",
        ],
        "Yamaha": [
            "FZ",
            "FZ-S",
            "FZ-X",
            "MT 15",
            "R15 V4",
            "R15S",
            "Aerox 155",
            "Fascino 125",
            "RayZR 125",
        ],
        "Suzuki": [
            "Access 125",
            "Avenis",
            "Burgman Street",
            "Gixxer",
            "Gixxer SF",
            "V-Strom SX",
            "Hayabusa",
        ],
        "Royal Enfield": [
            "Classic 350",
            "Bullet 350",
            "Hunter 350",
            "Meteor 350",
            "Himalayan",
            "Scram 411",
            "Interceptor 650",
            "Continental GT 650",
            "Super Meteor 650",
        ],
        "KTM": [
            "Duke 200",
            "Duke 250",
            "Duke 390",
            "RC 200",
            "RC 390",
            "Adventure 250",
            "Adventure 390",
        ],
        "Ola Electric": [
            "S1 X",
            "S1 Air",
            "S1 Pro",
        ],
        "Ather": [
            "450S",
            "450X",
            "Rizta",
        ],
    },

    "FOUR_WHEELER": {
        "Maruti Suzuki": [
            "Alto K10",
            "S-Presso",
            "Celerio",
            "Wagon R",
            "Swift",
            "Baleno",
            "Dzire",
            "Fronx",
            "Brezza",
            "Ertiga",
            "XL6",
            "Grand Vitara",
            "Jimny",
            "Invicto",
        ],
        "Hyundai": [
            "Grand i10 Nios",
            "i20",
            "Exter",
            "Aura",
            "Venue",
            "Creta",
            "Verna",
            "Alcazar",
            "Tucson",
            "Ioniq 5",
        ],
        "Tata": [
            "Tiago",
            "Tigor",
            "Altroz",
            "Punch",
            "Nexon",
            "Curvv",
            "Harrier",
            "Safari",
            "Tiago EV",
            "Tigor EV",
            "Punch EV",
            "Nexon EV",
        ],
        "Mahindra": [
            "Bolero",
            "Bolero Neo",
            "Thar",
            "Thar Roxx",
            "Scorpio Classic",
            "Scorpio N",
            "XUV 3XO",
            "XUV400",
            "XUV700",
            "Marazzo",
        ],
        "Toyota": [
            "Glanza",
            "Taisor",
            "Urban Cruiser Hyryder",
            "Rumion",
            "Innova Crysta",
            "Innova Hycross",
            "Fortuner",
            "Hilux",
            "Camry",
            "Vellfire",
        ],
        "Kia": [
            "Sonet",
            "Seltos",
            "Carens",
            "Carnival",
            "EV6",
            "EV9",
        ],
        "Honda": [
            "Amaze",
            "City",
            "City Hybrid",
            "Elevate",
        ],
        "MG": [
            "Comet EV",
            "Astor",
            "Hector",
            "Hector Plus",
            "ZS EV",
            "Gloster",
            "Windsor EV",
        ],
        "Skoda": [
            "Kylaq",
            "Kushaq",
            "Slavia",
            "Kodiaq",
            "Superb",
        ],
        "Volkswagen": [
            "Virtus",
            "Taigun",
            "Tiguan",
        ],
        "Nissan": [
            "Magnite",
            "X-Trail",
        ],
        "Renault": [
            "Kwid",
            "Triber",
            "Kiger",
        ],
        "Jeep": [
            "Compass",
            "Meridian",
            "Wrangler",
            "Grand Cherokee",
        ],
    },

    "HEAVY_VEHICLE": {
        "Tata Motors": [
            "Ace",
            "Intra V10",
            "Intra V30",
            "Intra V50",
            "407 Gold",
            "709",
            "Ultra T.7",
            "Ultra T.9",
            "Signa 1923",
            "Signa 2823",
            "Starbus",
            "Ultra Bus",
        ],
        "Ashok Leyland": [
            "Dost",
            "Bada Dost",
            "Partner",
            "Boss",
            "Ecomet",
            "AVTR 1920",
            "AVTR 2820",
            "AVTR 3520",
            "Viking",
            "Cheetah",
            "Oyster",
        ],
        "BharatBenz": [
            "1015R",
            "1217C",
            "1617R",
            "1923C",
            "2823C",
            "3528C",
            "Staff Bus",
            "School Bus",
        ],
        "Eicher": [
            "Pro 2049",
            "Pro 2059",
            "Pro 3015",
            "Pro 3019",
            "Pro 6028",
            "Skyline Pro",
            "Starline",
        ],
        "Volvo": [
            "9400",
            "9600",
            "FM 420",
            "FMX 460",
        ],
        "Mahindra": [
            "Jeeto",
            "Supro",
            "Bolero Pickup",
            "Furio 7",
            "Furio 11",
            "Blazo X 28",
            "Blazo X 35",
            "Cruzio",
        ],
    },
}


class Command(BaseCommand):
    help = "Seed vehicle companies and models into the database"

    def handle(self, *args, **options):
        company_created_count = 0
        model_created_count = 0

        for vehicle_type, companies in VEHICLE_DATA.items():
            for company_name, model_names in companies.items():
                company, company_created = VehicleCompany.objects.get_or_create(
                    name=company_name,
                    vehicle_type=vehicle_type,
                    defaults={"is_active": True},
                )

                if company_created:
                    company_created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Created company: {company_name} ({vehicle_type})"
                        )
                    )
                elif not company.is_active:
                    company.is_active = True
                    company.save(update_fields=["is_active"])

                for model_name in model_names:
                    _, model_created = VehicleModel.objects.get_or_create(
                        company=company,
                        name=model_name,
                        defaults={"is_active": True},
                    )

                    if model_created:
                        model_created_count += 1

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeding completed successfully."
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"New companies created: {company_created_count}"
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"New models created: {model_created_count}"
            )
        )