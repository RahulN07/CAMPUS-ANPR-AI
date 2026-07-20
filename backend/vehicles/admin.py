from django.contrib import admin

from .models import Vehicle, VehicleCompany, VehicleModel


@admin.register(VehicleCompany)
class VehicleCompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "vehicle_type",
        "is_active",
    )
    list_filter = (
        "vehicle_type",
        "is_active",
    )
    search_fields = ("name",)


@admin.register(VehicleModel)
class VehicleModelAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "company",
        "is_active",
    )
    list_filter = (
        "company",
        "is_active",
    )
    search_fields = (
        "name",
        "company__name",
    )


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = (
        "registration_number",
        "owner_name",
        "owner_type",
        "vehicle_company",
        "vehicle_model",
        "vehicle_type",
        "authorization_status",
    )

    list_filter = (
        "owner_type",
        "vehicle_type",
        "fuel_type",
        "authorization_status",
        "department",
    )

    search_fields = (
        "registration_number",
        "owner_name",
        "owner_email",
        "owner_phone",
        "vehicle_company",
        "vehicle_model",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )