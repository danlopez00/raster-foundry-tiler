package com.azavea.rasterfoundry

import com.amazonaws.auth.DefaultAWSCredentialsProviderChain
import com.amazonaws.services.sqs._
import com.amazonaws.services.sqs.model._
import com.amazonaws.regions.Regions

import org.apache.commons.io._
import java.io.{InputStream, ByteArrayOutputStream, ByteArrayInputStream, DataInputStream }
import scala.collection.JavaConverters._
import scala.collection.mutable
import com.typesafe.scalalogging.slf4j._

class Status(val queueUrl: String) extends LazyLogging {
  def sendNotification(msg: String): Unit = {
    val req = new SendMessageRequest(queueUrl, msg)
    val client = new AmazonSQSClient()
    val result = client.sendMessage(req)
    logger.info(s"MessageId: ${result.getMessageId}, Message: $msg");
  }

  def notifyStart(jobId: String): Unit =
    sendNotification(s"""{ "jobId": "$jobId", "stage": "mosaic", "status": "STARTED" }""")

  def notifyFailure(jobId: String, exception: Exception): Unit =
    sendNotification(s"""{ "jobId": "$jobId", "stage": "mosaic", "status": "FAILED", "error": "$exception" }""")

  def notifySuccess(jobId: String, target: String, sourceUris: Seq[String]): Unit = {
    val csv = sourceUris.map { uri => s""""$uri"""" }.mkString(",")
    val sourceUrisJson = s"[$csv]"
    sendNotification(s"""{ "jobId": "$jobId", "stage": "mosaic", "status": "FINISHED", "target": "$target", "images": $sourceUrisJson }""")
  }
}
