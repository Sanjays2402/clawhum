terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

provider "aws" {
  region = var.region
}

variable "region"      { type = string, default = "us-west-2" }
variable "name"        { type = string, default = "clawhum" }
variable "instance_type" { type = string, default = "t3.large" }
variable "ami_id"      { type = string }
variable "subnet_id"   { type = string }
variable "vpc_id"      { type = string }
variable "key_name"    { type = string }

resource "aws_security_group" "api" {
  name        = "${var.name}-api"
  description = "ClawHum API"
  vpc_id      = var.vpc_id
  ingress {
    from_port   = 7451
    to_port     = 7451
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "api" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [aws_security_group.api.id]
  key_name               = var.key_name
  tags = { Name = var.name, Service = "clawhum" }
}

output "public_ip" { value = aws_instance.api.public_ip }
